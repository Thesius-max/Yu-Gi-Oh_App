"""
Architektur-Invarianten aus CLAUDE.md, statisch per ast geprueft (kein Qt,
keine DB -- laeuft immer):

- yugioh_db ist reine Standardbibliothek (Standalone-Prinzip).
- Module der Datenschicht importieren einbahnig, ohne Zyklen.
- Views importieren einander nie (Querbezuege nur ueber Callbacks).
- Eigenes SQL gibt es in der GUI nur in repository.py.
"""

from __future__ import annotations

import ast
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PKG = ROOT / "yugioh_db"
GUI_PKG = ROOT / "yugioh_gui"


def _imports(path: Path) -> tuple[set[str], set[str]]:
    """(absolute Top-Level-Module, relative Modulnamen) einer Datei."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    absolute, relative = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            absolute |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                if node.module:
                    relative.add(node.module.split(".")[0])
                else:
                    relative |= {a.name for a in node.names}
            elif node.module:
                absolute.add(node.module.split(".")[0])
    return absolute, relative


class DataLayerTests(unittest.TestCase):
    def test_stdlib_only(self):
        for path in DB_PKG.glob("*.py"):
            absolute, _ = _imports(path)
            foreign = {m for m in absolute
                       if m not in sys.stdlib_module_names and m != "__future__"}
            self.assertEqual(foreign, set(), f"{path.name} importiert {foreign}")

    def test_layering_is_acyclic(self):
        graph = {}
        for path in DB_PKG.glob("*.py"):
            if path.stem in ("__init__", "__main__"):
                continue
            graph[path.stem] = _imports(path)[1] & {p.stem for p in DB_PKG.glob("*.py")}
        self.assertEqual(graph["schema"], set())          # Basis importiert nichts

        def visit(node, stack):
            self.assertNotIn(node, stack, f"Import-Zyklus: {stack + [node]}")
            for dep in graph.get(node, ()):
                visit(dep, stack + [node])

        for module in graph:
            visit(module, [])


class GuiLayerTests(unittest.TestCase):
    VIEWS = {"collection", "deck", "combos", "playtest", "manual"}

    def test_views_do_not_import_each_other(self):
        for view in self.VIEWS:
            _, relative = _imports(GUI_PKG / f"{view}.py")
            self.assertEqual(relative & (self.VIEWS - {view}), set(), view)

    def test_leaf_modules_import_no_views(self):
        for leaf in ("theme", "labels", "tasks", "images", "exporting",
                     "repository", "notation", "carddetail", "_cardinst",
                     "_game", "_rules", "playtest_dialogs"):
            _, relative = _imports(GUI_PKG / f"{leaf}.py")
            self.assertEqual(relative & (self.VIEWS | {"mainwindow", "app"}), set(), leaf)

    SQL = re.compile(
        r"\bSELECT\b.+\bFROM\b|\bINSERT\s+INTO\b|\bUPDATE\s+\w+\s+SET\b|\bDELETE\s+FROM\b",
        re.S,
    )

    def test_raw_sql_only_in_repository(self):
        for path in GUI_PKG.glob("*.py"):
            if path.name == "repository.py":
                continue
            source = path.read_text(encoding="utf-8")
            absolute, _ = _imports(path)
            self.assertNotIn("sqlite3", absolute, path.name)
            for token in ("._connect(", "._conn("):
                self.assertFalse(token in source, f"{path.name}: {token}")
            strings = [n.value for n in ast.walk(ast.parse(source))
                       if isinstance(n, ast.Constant) and isinstance(n.value, str)]
            sql = [s for s in strings if self.SQL.search(s)]
            self.assertEqual(sql, [], path.name)

    def test_repository_is_the_sql_boundary(self):
        # Gegenprobe: die Regel greift ueberhaupt (repository hat SQL).
        source = (GUI_PKG / "repository.py").read_text(encoding="utf-8")
        self.assertTrue(self.SQL.search(source))

    def test_gui_only_external_dependency_is_pyside6(self):
        for path in GUI_PKG.glob("*.py"):
            absolute, _ = _imports(path)
            foreign = {m for m in absolute if m not in sys.stdlib_module_names
                       and m not in ("__future__", "PySide6", "yugioh_db")}
            self.assertEqual(foreign, set(), f"{path.name} importiert {foreign}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
