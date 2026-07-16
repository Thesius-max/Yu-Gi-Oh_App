"""Kartenbild-Infrastruktur: Prozess-Cache, Skalierung, Hover-Vorschau.

Eine Quelle fuer die "Bilder nie hotlinken"-Regel: lookup_card_pixmap
liest nur, was cache_image() bereits heruntergeladen hat; fehlende
Bilder laedt der ImageLoader asynchron nach. Dazu das Mixin
CardImageView (grosses Standbild) und programmatisch gezeichnete
Pixmaps fuer das Spielfeld (Kartenruecken, Platzhalter).
"""

from __future__ import annotations

import os

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, Qt, QThreadPool
from PySide6.QtGui import (
    QBrush, QColor, QGuiApplication, QImage, QPainter, QPen, QPixmap,
    QPixmapCache,
)
from PySide6.QtWidgets import QLabel

import yugioh_db as ydb

from .tasks import ImageLoader, ImageSignals

# Standard-Bildquelle. Bilder werden lokal zwischengespeichert (kein Hotlinking).
# Bei Bedarf gegen die in der API gelieferte card_images-URL austauschbar.
IMAGE_URL = "https://images.ygoprodeck.com/images/cards/{}.jpg"


def scale_pixmap(pix: QPixmap, w: int, h: int) -> QPixmap:
    """Pixmap auf w×h einpassen (Seitenverhaeltnis erhalten, glatt)."""
    return pix.scaled(
        w, h,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def lookup_card_pixmap(card_id: int, w: int, h: int, prefix: str):
    """Skaliertes Kartenbild aus dem Prozess-Cache oder der lokalen Datei
    (dann skaliert + gecacht), sonst None -> der Aufrufer laedt asynchron via
    ImageLoader nach. Eine Quelle fuer die „Bilder nie hotlinken"-Regel: es
    wird nur gelesen, was cache_image() bereits heruntergeladen hat. 'prefix'
    trennt die Cache-Eintraege je Darstellungsgroesse ('card'/'detail'/'hover')."""
    key = f"{prefix}:{card_id}"
    cached = QPixmapCache.find(key)
    if cached is not None and not cached.isNull():
        return cached
    path = os.path.join(ydb.IMAGE_DIR, f"{card_id}.jpg")
    if os.path.exists(path):
        pix = QPixmap(path)
        if not pix.isNull():
            pix = scale_pixmap(pix, w, h)
            QPixmapCache.insert(key, pix)
            return pix
    return None


class CardImageView:
    """Mixin fuer Karten-Panels mit grossem Standbild (DetailPanel,
    CardDetailDialog). Erwartet self.image (QLabel) und self.current_id;
    Unterklassen setzen IMG_W/IMG_H und _CACHE_PREFIX. So teilen sich beide
    das Cache-/Disk-/Async-Laden ueber den gemeinsamen Bildcache."""

    IMG_W, IMG_H = 220, 320
    _CACHE_PREFIX = "card"

    def _init_card_image(self) -> None:
        """Im __init__ der Unterklasse aufrufen (richtet die Lade-Signale ein)."""
        self._img_signals = ImageSignals()
        self._img_signals.loaded.connect(self._on_image_loaded)
        self._img_signals.failed.connect(self._on_image_failed)

    def _load_image(self, card_id: int) -> None:
        pix = lookup_card_pixmap(
            card_id, self.IMG_W, self.IMG_H, self._CACHE_PREFIX
        )
        if pix is not None:
            self.image.setPixmap(pix)
            return
        self.image.setText("Lade …")
        QThreadPool.globalInstance().start(
            ImageLoader(card_id, IMAGE_URL.format(card_id), self._img_signals)
        )

    def _on_image_loaded(self, card_id: int, img: QImage) -> None:
        pix = scale_pixmap(QPixmap.fromImage(img), self.IMG_W, self.IMG_H)
        QPixmapCache.insert(f"{self._CACHE_PREFIX}:{card_id}", pix)
        if card_id == self.current_id:
            self.image.setPixmap(pix)

    def _on_image_failed(self, card_id: int) -> None:
        if card_id == self.current_id:
            self.image.setText("(Bild offline nicht verfügbar)")


class HoverCardPreview(QObject):
    """Schwebende Kartenbild-Vorschau fuer Item-Views (QListWidget/
    QTableWidget). Faehrt die Maus ueber eine Zeile mit zugeordneter Karte,
    erscheint neben dem Cursor ein lazy geladenes Kartenbild -- dieselbe
    Quelle und derselbe Cache wie das grosse Detailbild (card_images/,
    QPixmapCache). Beruehrt die Datenschicht nicht.

    'resolver(pos)' liefert die card_id unter einer Viewport-Position oder
    None (Kopfzeilen/Leerraum -> keine Vorschau). Eine Instanz je View; sie
    haengt sich per Eventfilter an dessen Viewport und raeumt mit der View ab
    (QObject-Parent)."""

    PREVIEW_W, PREVIEW_H = 200, 292

    def __init__(self, view, resolver):
        super().__init__(view)
        self._view = view
        self._resolver = resolver
        self._current = None          # gezeigte/erwartete card_id
        self._last_pos = QPoint()     # letzte globale Cursorposition

        self._popup = QLabel(view)
        self._popup.setObjectName("HoverCardPreview")
        self._popup.setWindowFlags(Qt.WindowType.ToolTip)
        self._popup.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._popup.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._popup.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._popup.hide()

        self._signals = ImageSignals()
        self._signals.loaded.connect(self._on_loaded)
        self._signals.failed.connect(self._on_failed)

        view.setMouseTracking(True)
        view.viewport().setMouseTracking(True)
        view.viewport().installEventFilter(self)

    def eventFilter(self, obj, event):
        et = event.type()
        if et == QEvent.Type.MouseMove:
            self._last_pos = event.globalPosition().toPoint()
            cid = self._resolver(event.position().toPoint())
            if cid != self._current:
                self._update(cid)
            elif cid is not None and self._popup.isVisible():
                self._place()
        elif et in (
            QEvent.Type.Leave, QEvent.Type.Hide, QEvent.Type.Wheel,
            QEvent.Type.MouseButtonPress,
        ):
            self._hide()
        return False

    def _update(self, cid) -> None:
        self._current = cid
        if cid is None:
            self._hide()
            return
        pix = lookup_card_pixmap(cid, self.PREVIEW_W, self.PREVIEW_H, "hover")
        if pix is not None:
            self._show_pixmap(pix)
            return
        # Noch nicht lokal -> Platzhalter zeigen, im Hintergrund nachladen.
        self._popup.setPixmap(QPixmap())
        self._popup.setText("Lade …")
        self._popup.setFixedSize(self.PREVIEW_W, self.PREVIEW_H)
        self._place()
        self._popup.show()
        QThreadPool.globalInstance().start(
            ImageLoader(cid, IMAGE_URL.format(cid), self._signals)
        )

    def _show_pixmap(self, pix: QPixmap) -> None:
        self._popup.setText("")
        self._popup.setPixmap(pix)
        self._popup.setFixedSize(pix.size())
        self._place()
        self._popup.show()

    def _on_loaded(self, card_id: int, img: QImage) -> None:
        pix = scale_pixmap(QPixmap.fromImage(img), self.PREVIEW_W, self.PREVIEW_H)
        QPixmapCache.insert(f"hover:{card_id}", pix)
        if card_id == self._current and self._popup.isVisible():
            self._show_pixmap(pix)

    def _on_failed(self, card_id: int) -> None:
        if card_id == self._current and self._popup.isVisible():
            self._popup.setText("(Bild offline nicht verfügbar)")

    def _hide(self) -> None:
        self._current = None
        self._popup.hide()

    def _place(self) -> None:
        pt = self._last_pos
        w, h = self._popup.width(), self._popup.height()
        screen = QGuiApplication.screenAt(pt) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        x = pt.x() + 24
        if x + w > geo.right():
            x = pt.x() - w - 24            # nach links kippen, wenn rechts kein Platz
        y = max(geo.top(), min(pt.y() - h // 2, geo.bottom() - h))
        self._popup.move(x, y)


def card_back_pixmap(w: int, h: int) -> QPixmap:
    """Programmatisch gezeichnete Kartenrueckseite (kein Asset), gecacht."""
    key = f"cardback:{w}x{h}"
    pm = QPixmapCache.find(key)
    if pm is not None and not pm.isNull():
        return pm
    pm = QPixmap(w, h)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QBrush(QColor("#3a2f4d")))
    p.setPen(QPen(QColor("#d4af37"), 2))
    p.drawRoundedRect(1, 1, w - 2, h - 2, 6, 6)
    p.setBrush(QBrush(QColor("#241a33")))
    cx, cy, d = w / 2, h / 2, min(w, h) * 0.26
    p.drawPolygon([QPointF(cx, cy - d), QPointF(cx + d, cy),
                   QPointF(cx, cy + d), QPointF(cx - d, cy)])
    p.end()
    QPixmapCache.insert(key, pm)
    return pm


def placeholder_pixmap(name: str, w: int, h: int) -> QPixmap:
    """Grauer Platzhalter mit Kartennamen, solange das Bild noch laedt/fehlt."""
    pm = QPixmap(w, h)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QBrush(QColor("#2c2340")))
    p.setPen(QPen(QColor("#6f6786"), 1))
    p.drawRoundedRect(0, 0, w - 1, h - 1, 5, 5)
    p.setPen(QColor("#cfc6e0"))
    f = p.font(); f.setPointSize(6); p.setFont(f)
    p.drawText(
        pm.rect().adjusted(3, 3, -3, -3),
        int(Qt.AlignmentFlag.AlignCenter) | int(Qt.TextFlag.TextWordWrap),
        (name or "")[:40],
    )
    p.end()
    return pm
