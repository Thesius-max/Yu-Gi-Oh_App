"""Hintergrund-Arbeiten abseits des UI-Threads (QThreadPool-Runnables).

ImageLoader laedt Kartenbilder einmalig lokal (ydb.cache_image, kein
Hotlinking); DbTask fuehrt laengere DB-/Netzwerkfunktionen aus (z.B. das
Kartendaten-Update) und meldet per Signal zurueck.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, Signal
from PySide6.QtGui import QImage

import yugioh_db as ydb


# ---------------------------------------------------------------------------
# Asynchroner Bild-Loader
# ---------------------------------------------------------------------------

class ImageSignals(QObject):
    loaded = Signal(int, QImage)  # (card_id, image)
    failed = Signal(int)          # card_id


class ImageLoader(QRunnable):
    def __init__(self, card_id: int, url: str, signals: ImageSignals):
        super().__init__()
        self._card_id = card_id
        self._url = url
        self._signals = signals
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            path = ydb.cache_image(self._card_id, self._url)
            img = QImage(str(path))
            if img.isNull():
                self._signals.failed.emit(self._card_id)
            else:
                self._signals.loaded.emit(self._card_id, img)
        except RuntimeError:
            pass  # Empfaenger beim App-Ende schon abgebaut -- nichts zu melden
        except Exception:
            try:
                self._signals.failed.emit(self._card_id)
            except RuntimeError:
                pass


class DbTaskSignals(QObject):
    done = Signal(object)
    failed = Signal(str)


class DbTask(QRunnable):
    """Fuehrt eine laengere DB-/Netzwerkfunktion abseits des UI-Threads aus
    (z.B. das Kartendaten-Update); Ergebnis kommt per Signal zurueck."""

    def __init__(self, fn, signals: DbTaskSignals):
        super().__init__()
        self._fn = fn
        self._signals = signals
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            result = self._fn()
        except Exception as exc:
            try:
                self._signals.failed.emit(str(exc))
            except RuntimeError:
                pass
            return
        try:
            self._signals.done.emit(result)
        except RuntimeError:
            pass
