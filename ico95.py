#!/usr/bin/env python3
"""ico95: paint multi-image .ico files for Windows 95 programs.

Usage: python3 ico95.py [file.ico]
"""

import os
import sys

from PyQt6.QtCore import QRect, QSize, Qt # type: ignore[import-not-found]
from PyQt6.QtGui import ( # type: ignore[import-not-found]
    QAction, QActionGroup, QBrush, QColor, QIcon, QImage, QKeySequence,
    QPainter, QPalette, QPixmap, qRgb,
)
from PyQt6.QtWidgets import (  # type: ignore[import-not-found]
    QApplication, QColorDialog, QComboBox, QDialog,
    QDialogButtonBox, QFileDialog, QFormLayout, QGridLayout, QHBoxLayout,
    QInputDialog, QLabel, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QPushButton, QScrollArea, QSizePolicy, QStyle, QToolButton,
    QVBoxLayout, QWidget,
)

import icoformat as ico
import raster
from icoformat import INVERT, TRANSPARENT, IconImage

APP_NAME = "ico95"

BACKGROUNDS = [
    ("Desktop teal", (0, 128, 128)),
    ("Window gray", (192, 192, 192)),
    ("White", (255, 255, 255)),
    ("Black", (0, 0, 0)),
]

TOOLS = [
    # (id, label, shortcut)
    ("pencil", "Pencil", "P"),
    ("eraser", "Eraser", "E"),
    ("line", "Line", "L"),
    ("fill", "Fill", "F"),
    ("rect", "Rectangle", "R"),
    ("frect", "Filled rect", "Shift+R"),
    ("ellipse", "Ellipse", "O"),
    ("fellipse", "Filled ellipse", "Shift+O"),
    ("picker", "Pick color", "K"),
]
SHAPE_TOOLS = {"line", "rect", "frect", "ellipse", "fellipse"}

ZOOMS = [2, 3, 4, 6, 8, 10, 12, 16, 20, 24, 32]

_brushes = {}


def special_brush(value):
    """Checkerboard for transparent, black/white hatching for inverted.
    Created lazily because pixmaps need a running QApplication."""
    if value not in _brushes:
        pm = QPixmap(8, 8)
        p = QPainter(pm)
        if value == TRANSPARENT:
            pm.fill(QColor(255, 255, 255))
            p.fillRect(0, 0, 4, 4, QColor(200, 200, 200))
            p.fillRect(4, 4, 4, 4, QColor(200, 200, 200))
        else:
            pm.fill(QColor(0, 0, 0))
            for i in range(8):
                p.fillRect(7 - i, i, 1, 1, QColor(255, 255, 255))
        p.end()
        _brushes[value] = QBrush(pm)
    return _brushes[value]


def paint_value(p, rect, value, palette):
    if value < 0:
        p.fillRect(rect, special_brush(value))
    else:
        p.fillRect(rect, QColor(*palette[value]))


def describe(value, palette):
    if value == TRANSPARENT:
        return "Transparent (screen)"
    if value == INVERT:
        return "Inverted screen"
    r, g, b = palette[value]
    return f"#{value} ({r}, {g}, {b})"


def to_qimage(img, bg):
    """Render the icon the way Windows draws it on a solid background."""
    q = QImage(img.size, img.size, QImage.Format.Format_RGB32)
    inverted = tuple(255 - c for c in bg)
    for i, v in enumerate(img.pixels):
        rgb = bg if v == TRANSPARENT else inverted if v == INVERT else img.palette[v]
        q.setPixel(i % img.size, i // img.size, qRgb(*rgb))
    return q


def retro_palette():
    pal = QPalette()
    gray, dark, white, black = (QColor(192, 192, 192), QColor(128, 128, 128),
                                QColor(255, 255, 255), QColor(0, 0, 0))
    R = QPalette.ColorRole
    for role, color in [
        (R.Window, gray), (R.Button, gray), (R.Base, white),
        (R.AlternateBase, QColor(224, 224, 224)), (R.WindowText, black),
        (R.ButtonText, black), (R.Text, black), (R.BrightText, white),
        (R.Light, white), (R.Midlight, QColor(223, 223, 223)), (R.Mid, dark),
        (R.Dark, dark), (R.Shadow, black), (R.Highlight, QColor(0, 0, 128)),
        (R.HighlightedText, white), (R.ToolTipBase, QColor(255, 255, 225)),
        (R.ToolTipText, black), (R.PlaceholderText, dark),
    ]:
        pal.setColor(role, color)
    for role in (R.WindowText, R.ButtonText, R.Text):
        pal.setColor(QPalette.ColorGroup.Disabled, role, dark)
    return pal


# --- widgets --------------------------------------------------------------

class Canvas(QWidget):
    """The zoomed pixel grid of the current image."""

    def __init__(self, editor):
        super().__init__()
        self.editor = editor
        self.zoom = 16
        self.show_grid = True
        self.drag = None        # (button, start point, colour value)
        self.base = None        # pixels before the current drag
        self.preview = None     # pixels shown while dragging
        self.last = None
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)

    @property
    def image(self):
        return self.editor.image

    def update_size(self):
        side = self.image.size * self.zoom + 1
        self.setFixedSize(side, side)
        self.update()

    def cell(self, pos):
        return int(pos.x() // self.zoom), int(pos.y() // self.zoom)

    def paintEvent(self, _):
        img, z = self.image, self.zoom
        pixels = self.preview or img.pixels
        p = QPainter(self)
        for i, v in enumerate(pixels):
            paint_value(p, QRect(i % img.size * z, i // img.size * z, z, z), v, img.palette)
        side = img.size * z
        if self.show_grid and z >= 6:
            p.setPen(QColor(128, 128, 128, 110))
            for k in range(1, img.size):
                p.drawLine(k * z, 0, k * z, side)
                p.drawLine(0, k * z, side, k * z)
        p.setPen(QColor(0, 0, 0))
        p.drawRect(0, 0, side, side)

    def _plot(self, pixels, points, value):
        size = self.image.size
        for x, y in points:
            if 0 <= x < size and 0 <= y < size:
                pixels[y * size + x] = value

    def _shape(self, tool, start, end, square):
        (x0, y0), (x1, y1) = start, end
        if square and tool != "line":
            side = max(abs(x1 - x0), abs(y1 - y0))
            x1 = x0 + (side if x1 >= x0 else -side)
            y1 = y0 + (side if y1 >= y0 else -side)
        if tool == "line":
            return raster.line(x0, y0, x1, y1)
        if tool in ("rect", "frect"):
            return raster.rectangle(x0, y0, x1, y1, filled=tool == "frect")
        return raster.ellipse(x0, y0, x1, y1, filled=tool == "fellipse")

    def mousePressEvent(self, e):
        button = e.button()
        if self.drag or button not in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton):
            return
        x, y = self.cell(e.position())
        size = self.image.size
        if not (0 <= x < size and 0 <= y < size):
            return
        tool = self.editor.tool
        value = TRANSPARENT if tool == "eraser" else self.editor.colors[button]
        if tool == "picker":
            self.editor.set_color(button, self.image.pixels[y * size + x])
            return
        if tool == "fill":
            self.editor.apply(raster.flood_fill(self.image.pixels, size, x, y, value))
            return
        self.drag = (button, (x, y), value)
        self.base = list(self.image.pixels)
        self.last = (x, y)
        self.preview = list(self.base)
        if tool in SHAPE_TOOLS:
            self._plot(self.preview, self._shape(tool, (x, y), (x, y), False), value)
        else:
            self._plot(self.preview, [(x, y)], value)
        self.update()

    def mouseMoveEvent(self, e):
        x, y = self.cell(e.position())
        self.editor.show_hover(x, y)
        if not self.drag or self.base is None or self.last is None:
            return
        _, start, value = self.drag
        tool = self.editor.tool
        if tool in SHAPE_TOOLS:
            square = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            self.preview = list(self.base)
            self._plot(self.preview, self._shape(tool, start, (x, y), square), value)
        else:
            self._plot(self.preview, raster.line(self.last[0], self.last[1], x, y), value)
            self.last = (x, y)
        self.update()

    def mouseReleaseEvent(self, e):
        if not self.drag or e.button() != self.drag[0]:
            return
        pixels = self.preview
        self.drag = self.preview = self.base = None
        self.editor.apply(pixels)

    def leaveEvent(self, _):
        self.editor.show_hover(-1, -1)


class CanvasScrollArea(QScrollArea):
    """Ctrl+wheel zooms anywhere over the canvas or the gray area around it
    (wheel events over the canvas propagate up to here)."""

    def __init__(self, editor):
        super().__init__()
        self.editor = editor

    def wheelEvent(self, e):
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.editor.zoom_by(1 if e.angleDelta().y() > 0 else -1)
            e.accept()
        else:
            super().wheelEvent(e)


class PaletteView(QWidget):
    """Transparent / inverted swatches plus the image's colour table.
    Left click picks the left colour, right click the right colour,
    double click edits a 256-colour palette entry."""

    SPECIAL_H = 22

    def __init__(self, editor):
        super().__init__()
        self.editor = editor
        self.setFixedWidth(16 * 14 + 1)

    def cells(self):
        """(rect, value) for every swatch."""
        palette = self.editor.image.palette
        cols, cell = (16, 14) if len(palette) > 16 else (8, 28)
        half = self.width() // 2
        out = [(QRect(0, 0, half - 2, self.SPECIAL_H), TRANSPARENT),
               (QRect(half + 1, 0, half - 2, self.SPECIAL_H), INVERT)]
        top = self.SPECIAL_H + 6
        for i in range(len(palette)):
            out.append((QRect(i % cols * cell, top + i // cols * cell, cell, cell), i))
        return out

    def sizeHint(self):
        return QSize(self.width(), self.cells()[-1][0].bottom() + 2)

    def refresh(self):
        self.setFixedHeight(self.sizeHint().height())
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        palette = self.editor.image.palette
        left = self.editor.colors[Qt.MouseButton.LeftButton]
        right = self.editor.colors[Qt.MouseButton.RightButton]
        for rect, value in self.cells():
            paint_value(p, rect, value, palette)
            p.setPen(QColor(128, 128, 128))
            p.drawRect(rect.adjusted(0, 0, -1, -1))
            if value < 0:
                label = "Transparent" if value == TRANSPARENT else "Inverted"
                p.fillRect(QRect(rect.x() + 3, rect.y() + 3, p.fontMetrics().horizontalAdvance(label) + 6,
                                 rect.height() - 6), QColor(255, 255, 255, 220))
                p.setPen(QColor(0, 0, 0))
                p.drawText(rect.adjusted(6, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, label)
            for chosen, color, inset in ((left, QColor(255, 0, 0), 0), (right, QColor(0, 0, 255), 2)):
                if value == chosen:
                    p.setPen(color)
                    p.drawRect(rect.adjusted(inset, inset, -1 - inset, -1 - inset))
                    p.drawRect(rect.adjusted(inset + 1, inset + 1, -2 - inset, -2 - inset))

    def value_at(self, pos):
        for rect, value in self.cells():
            if rect.contains(pos):
                return value
        return None

    def mousePressEvent(self, e):
        value = self.value_at(e.position().toPoint())
        if value is not None and e.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton):
            self.editor.set_color(e.button(), value)

    def mouseDoubleClickEvent(self, e):
        value = self.value_at(e.position().toPoint())
        if value is not None and value >= 0 and e.button() == Qt.MouseButton.LeftButton:
            self.editor.edit_palette_entry(value)


class ColorWells(QWidget):
    """Shows what the left and right mouse buttons paint with."""

    def __init__(self, editor):
        super().__init__()
        self.editor = editor
        self.setFixedHeight(48)

    def paintEvent(self, _):
        p = QPainter(self)
        palette = self.editor.image.palette
        for row, (name, button) in enumerate((("Left", Qt.MouseButton.LeftButton),
                                              ("Right", Qt.MouseButton.RightButton))):
            value = self.editor.colors[button]
            y = row * 24
            p.setPen(QColor(0, 0, 0))
            p.drawText(QRect(0, y, 40, 20), Qt.AlignmentFlag.AlignVCenter, name)
            swatch = QRect(42, y + 1, 34, 18)
            paint_value(p, swatch, value, palette)
            p.drawRect(swatch.adjusted(0, 0, -1, -1))
            p.drawText(QRect(84, y, self.width() - 84, 20), Qt.AlignmentFlag.AlignVCenter,
                       describe(value, palette))


class PreviewView(QWidget):
    """All images at actual size and 2x on the chosen background."""

    PAD = 8

    def __init__(self, editor):
        super().__init__()
        self.editor = editor

    def refresh(self):
        images = self.editor.images
        tallest = max(img.size for img in images)
        width = self.PAD + sum(img.size * 2 + self.PAD for img in images)
        self.setMinimumSize(width, self.PAD * 3 + tallest * 3)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        bg = self.editor.preview_bg
        p.fillRect(self.rect(), QColor(*bg))
        images = self.editor.images
        tallest = max(img.size for img in images)
        x = self.PAD
        for img in images:
            q = to_qimage(img, bg)
            p.drawImage(x, self.PAD, q)
            p.drawImage(QRect(x, self.PAD * 2 + tallest, img.size * 2, img.size * 2), q)
            x += img.size * 2 + self.PAD


class AddImageDialog(QDialog):
    def __init__(self, parent, images):
        super().__init__(parent)
        self.setWindowTitle("Add image")
        self.images = images
        form = QFormLayout(self)
        self.size_box = QComboBox()
        for s in ico.SIZES:
            note = "" if s in ico.WIN95_SIZES else "  (Windows 98 and later)"
            self.size_box.addItem(f"{s}×{s}{note}", s)
        self.depth_box = QComboBox()
        for bpp, note in ((4, "  (standard for Windows 95)"), (8, ""), (1, "")):
            self.depth_box.addItem(ico.DEPTH_NAMES[bpp] + note, bpp)
        self.source_box = QComboBox()
        self.source_box.addItem("Blank (transparent)", None)
        for img in images:
            self.source_box.addItem(f"Scaled copy of {img.label}", img)
        form.addRow("Size:", self.size_box)
        form.addRow("Colors:", self.depth_box)
        form.addRow("Start from:", self.source_box)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        # Preselect the first size/depth combination the icon lacks.
        existing = {img.key for img in images}
        for s in (32, 16, 48):
            for bpp in (4, 8, 1):
                if (s, bpp) not in existing:
                    self.size_box.setCurrentIndex(self.size_box.findData(s))
                    self.depth_box.setCurrentIndex(self.depth_box.findData(bpp))
                    return

    def result_image(self, dither):
        size, bpp = self.size_box.currentData(), self.depth_box.currentData()
        source = self.source_box.currentData()
        return source.converted(size, bpp, dither) if source else IconImage.blank(size, bpp)


# --- main window ----------------------------------------------------------

class Editor(QMainWindow):
    def __init__(self):
        super().__init__()
        self.images = []
        self.current = 0
        self.path = None
        self.dirty = False
        self.undo_stack, self.redo_stack = [], []
        self.colors = {Qt.MouseButton.LeftButton: 0, Qt.MouseButton.RightButton: 0}
        self.preview_bg = BACKGROUNDS[0][1]
        self.setAcceptDrops(True)

        self.canvas = Canvas(self)
        self.palette_view = PaletteView(self)
        self.wells = ColorWells(self)
        self.preview = PreviewView(self)
        self._build_ui()
        self._build_menus()
        self.new_icon(confirm=False)
        self.resize(1180, 760)

    # state helpers

    @property
    def image(self):
        return self.images[self.current]

    @property
    def tool(self):
        return self.tool_group.checkedAction().data()

    def snapshot(self):
        return [img.copy() for img in self.images], self.current

    def push_undo(self):
        self.undo_stack = self.undo_stack[-199:] + [self.snapshot()]
        self.redo_stack.clear()

    def apply(self, pixels):
        """Replace the current image's pixels as one undoable step."""
        if pixels != self.image.pixels:
            self.push_undo()
            self.image.pixels = pixels
            self.changed()
        else:
            self.canvas.update()

    def changed(self):
        self.dirty = True
        self.refresh()

    def set_color(self, button, value):
        self.colors[button] = value
        self.wells.update()
        self.palette_view.update()

    def _default_colors(self):
        palette = self.image.palette
        self.colors[Qt.MouseButton.LeftButton] = palette.index(ico.BLACK) if ico.BLACK in palette else 0
        self.colors[Qt.MouseButton.RightButton] = palette.index(ico.WHITE) if ico.WHITE in palette else 0

    def _remap_colors(self, old_palette):
        for button, value in self.colors.items():
            if value >= 0:
                rgb = old_palette[value] if value < len(old_palette) else ico.BLACK
                self.colors[button] = ico.nearest_index(self.image.palette, rgb)

    def select_image(self, index):
        if index < 0 or index >= len(self.images) or index == self.current:
            return
        old_palette = self.image.palette
        self.current = index
        self._remap_colors(old_palette)
        self.fit_zoom()
        self.refresh()

    # UI construction

    def _build_ui(self):
        central = QWidget()
        layout = QHBoxLayout(central)

        left = QVBoxLayout()
        left.addWidget(QLabel("<b>Tools</b>"))
        self.tool_grid = QGridLayout()
        left.addLayout(self.tool_grid)
        left.addSpacing(8)
        left.addWidget(QLabel("<b>Colors</b>"))
        left.addWidget(self.wells)
        left.addWidget(self.palette_view)
        self.palette_note = QLabel()
        self.palette_note.setWordWrap(True)
        self.palette_note.setFixedWidth(self.palette_view.width())
        left.addWidget(self.palette_note)
        left.addStretch()
        layout.addLayout(left)

        scroll = CanvasScrollArea(self)
        scroll.setWidget(self.canvas)
        scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scroll.setBackgroundRole(QPalette.ColorRole.Dark)
        layout.addWidget(scroll, 1)

        right = QVBoxLayout()
        right.addWidget(QLabel("<b>Images in this icon</b>"))
        self.image_list = QListWidget()
        self.image_list.setIconSize(QSize(52, 52))
        self.image_list.setFixedWidth(300)
        self.image_list.currentRowChanged.connect(self.select_image)
        right.addWidget(self.image_list, 2)
        buttons = QHBoxLayout()
        for text, slot in (("Add…", self.add_image), ("Remove", self.remove_image),
                           ("Redraw from…", self.replace_from)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            buttons.addWidget(b)
        right.addLayout(buttons)
        right.addSpacing(8)

        bg_row = QHBoxLayout()
        bg_row.addWidget(QLabel("<b>Preview</b> on"))
        self.bg_box = QComboBox()
        for name, rgb in BACKGROUNDS:
            self.bg_box.addItem(name, rgb)
        self.bg_box.currentIndexChanged.connect(self._set_background)
        bg_row.addWidget(self.bg_box, 1)
        right.addLayout(bg_row)
        preview_scroll = QScrollArea()
        preview_scroll.setWidget(self.preview)
        preview_scroll.setWidgetResizable(True)
        preview_scroll.setFixedWidth(300)
        preview_scroll.setFixedHeight(180)
        right.addWidget(preview_scroll)
        right.addSpacing(8)

        right.addWidget(QLabel("<b>Windows 95 check</b>"))
        self.check_list = QListWidget()
        self.check_list.setWordWrap(True)
        self.check_list.setFixedWidth(300)
        right.addWidget(self.check_list, 3)
        self.fill_button = QPushButton("Fill In Missing Images")
        self.fill_button.setToolTip("Create the 16-color images Windows 95 needs from the "
                                    "existing ones")
        self.fill_button.clicked.connect(self.fill_in_missing)
        right.addWidget(self.fill_button)
        layout.addLayout(right)

        self.setCentralWidget(central)
        self.hover_label = QLabel()
        self.zoom_label = QLabel()
        self.statusBar().addWidget(self.hover_label, 1)
        self.statusBar().addPermanentWidget(self.zoom_label)

    def _action(self, menu, text, slot, shortcut=None):
        action = QAction(text, self)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(slot)
        menu.addAction(action)
        return action

    def _build_menus(self):
        mb = self.menuBar()
        m = mb.addMenu("&File")
        self._action(m, "&New", lambda: self.new_icon(), QKeySequence.StandardKey.New)
        self._action(m, "&Open…", lambda: self.open_icon(), QKeySequence.StandardKey.Open)
        self._action(m, "&Save", self.save, QKeySequence.StandardKey.Save)
        self._action(m, "Save &As…", self.save_as, QKeySequence.StandardKey.SaveAs)
        m.addSeparator()
        self._action(m, "&Import Picture into Image…", self.import_picture, "Ctrl+I")
        m.addSeparator()
        self._action(m, "E&xit", self.close, QKeySequence.StandardKey.Quit)

        m = mb.addMenu("&Edit")
        self.undo_action = self._action(m, "&Undo", self.undo, QKeySequence.StandardKey.Undo)
        self.redo_action = self._action(m, "&Redo", self.redo, QKeySequence.StandardKey.Redo)
        m.addSeparator()
        self._action(m, "&Clear Image", lambda: self.apply([TRANSPARENT] * len(self.image.pixels)),
                     "Delete")
        m.addSeparator()
        self._action(m, "Flip &Horizontal", lambda: self.transform(raster.flip_horizontal), "Ctrl+Shift+H")
        self._action(m, "Flip &Vertical", lambda: self.transform(raster.flip_vertical), "Ctrl+Shift+V")

        m = mb.addMenu("&Image")
        self._action(m, "&Add Image…", self.add_image, "Ctrl+Shift+N")
        self._action(m, "&Remove Image", self.remove_image)
        self._action(m, "Re&draw from Another Image…", self.replace_from)
        self.fill_action = self._action(m, "&Fill In Missing Images", self.fill_in_missing,
                                        "Ctrl+Shift+F")
        self.dither_action = self._action(m, "D&ither When Reducing Colors", lambda: None)
        self.dither_action.setCheckable(True)

        m = mb.addMenu("&Tools")
        self.tool_group = QActionGroup(self)
        for i, (tool_id, label, key) in enumerate(TOOLS):
            action = self._action(m, label, lambda: None, key)
            action.setCheckable(True)
            action.setData(tool_id)
            self.tool_group.addAction(action)
            button = QToolButton()
            button.setDefaultAction(action)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.tool_grid.addWidget(button, i // 2, i % 2)
        self.tool_group.actions()[0].setChecked(True)

        m = mb.addMenu("&View")
        self._action(m, "Zoom &In", lambda: self.zoom_by(1), QKeySequence.StandardKey.ZoomIn)
        self._action(m, "Zoom &Out", lambda: self.zoom_by(-1), QKeySequence.StandardKey.ZoomOut)
        grid = self._action(m, "Show &Grid", self.toggle_grid, "Ctrl+G")
        grid.setCheckable(True)
        grid.setChecked(True)

    # refreshing

    def refresh(self):
        self.canvas.update_size()
        self.palette_view.refresh()
        self.wells.update()
        self.preview.refresh()

        self.image_list.blockSignals(True)
        self.image_list.clear()
        for img in self.images:
            thumb = QPixmap(52, 52)
            thumb.fill(QColor(*self.preview_bg))
            p = QPainter(thumb)
            offset = (52 - img.size) // 2
            p.drawImage(offset, offset, to_qimage(img, self.preview_bg))
            p.end()
            text = img.label if img.size in ico.WIN95_SIZES else img.label + "\n(not used by Win95)"
            self.image_list.addItem(QListWidgetItem(QIcon(thumb), text))
        self.image_list.setCurrentRow(self.current)
        self.image_list.blockSignals(False)

        errors, warnings = ico.validate(self.images)
        self.check_list.clear()
        style = self.style()
        for icon, messages in ((QStyle.StandardPixmap.SP_MessageBoxCritical, errors),
                               (QStyle.StandardPixmap.SP_MessageBoxWarning, warnings)):
            for msg in messages:
                self.check_list.addItem(QListWidgetItem(style.standardIcon(icon), msg))
        if not errors and not warnings:
            self.check_list.addItem(QListWidgetItem(
                style.standardIcon(QStyle.StandardPixmap.SP_DialogApplyButton),
                "Looks good for Windows 95."))

        locked = ico.palette_is_locked(self.image.bpp)
        self.palette_note.setText(
            "Fixed Windows palette." if locked else
            "Double-click a color to change it. Indices 0–9 and 246–255 are the "
            "Windows system colors; leave them alone for best results.")
        missing = bool(ico.missing_for_win95(self.images))
        self.fill_action.setEnabled(missing)
        self.fill_button.setEnabled(missing)
        self.undo_action.setEnabled(bool(self.undo_stack))
        self.redo_action.setEnabled(bool(self.redo_stack))
        self.zoom_label.setText(f"{self.image.label}   Zoom {self.canvas.zoom}×")
        name = os.path.basename(self.path) if self.path else "Untitled.ico"
        self.setWindowTitle(f"{name}{'*' if self.dirty else ''} — {APP_NAME}")

    def show_hover(self, x, y):
        size = self.image.size
        if 0 <= x < size and 0 <= y < size:
            value = self.image.pixels[y * size + x]
            self.hover_label.setText(f"{x}, {y}    {describe(value, self.image.palette)}")
        else:
            self.hover_label.clear()

    def _set_background(self):
        self.preview_bg = self.bg_box.currentData()
        self.refresh()

    def fit_zoom(self):
        target = 512 // self.image.size
        self.canvas.zoom = max(z for z in ZOOMS if z <= target)

    def zoom_by(self, step):
        i = ZOOMS.index(self.canvas.zoom) + step
        self.canvas.zoom = ZOOMS[max(0, min(len(ZOOMS) - 1, i))]
        self.refresh()

    def toggle_grid(self, on):
        self.canvas.show_grid = on
        self.canvas.update()

    # editing

    def transform(self, func):
        self.apply(func(self.image.pixels, self.image.size))

    def undo(self):
        if self.undo_stack:
            self.redo_stack.append(self.snapshot())
            self.images, self.current = self.undo_stack.pop()
            self._after_history()

    def redo(self):
        if self.redo_stack:
            self.undo_stack.append(self.snapshot())
            self.images, self.current = self.redo_stack.pop()
            self._after_history()

    def _after_history(self):
        for button, value in self.colors.items():
            if value >= len(self.image.palette):
                self.colors[button] = 0
        self.fit_zoom()
        self.changed()

    def add_image(self):
        dialog = AddImageDialog(self, self.images)
        if not dialog.exec():
            return
        img = dialog.result_image(self.dither)
        if img.key in {i.key for i in self.images}:
            QMessageBox.warning(self, APP_NAME, f"The icon already has a {img.label} image.")
            return
        self.push_undo()
        old_palette = self.image.palette
        self.images = sorted(self.images + [img], key=ico.sort_key)
        self.current = next(i for i, x in enumerate(self.images) if x is img)
        self._remap_colors(old_palette)
        self.fit_zoom()
        self.changed()

    def remove_image(self):
        if len(self.images) == 1:
            QMessageBox.information(self, APP_NAME, "An icon needs at least one image.")
            return
        self.push_undo()
        old_palette = self.image.palette
        del self.images[self.current]
        self.current = min(self.current, len(self.images) - 1)
        self._remap_colors(old_palette)
        self.fit_zoom()
        self.changed()

    @property
    def dither(self):
        return self.dither_action.isChecked()

    def fill_in_missing(self):
        images, new = ico.fill_missing(self.images, self.dither)
        if not new:
            self.statusBar().showMessage(
                "Nothing to fill in: draw an image first, or the icon is already complete.", 5000)
            return
        self.push_undo()
        current_key = self.image.key
        self.images = images
        self.current = next(i for i, img in enumerate(images) if img.key == current_key)
        self.changed()
        self.statusBar().showMessage(
            "Filled in " + ", ".join(img.label for img in new)
            + ". Converted images often need touching up by hand.", 10000)

    def replace_from(self):
        others = [img for img in self.images if img is not self.image]
        if not others:
            QMessageBox.information(self, APP_NAME, "There is no other image to copy from.")
            return
        labels = [img.label for img in others]
        choice, ok = QInputDialog.getItem(
            self, "Redraw from another image",
            f"Replace the {self.image.label} image with a scaled copy of:", labels, 0, False)
        if ok:
            source = others[labels.index(choice)]
            self.push_undo()
            self.images[self.current] = source.converted(self.image.size, self.image.bpp, self.dither)
            self.changed()

    def edit_palette_entry(self, index):
        if ico.palette_is_locked(self.image.bpp):
            self.statusBar().showMessage(
                "Monochrome and 16-color icons always use the standard Windows palette.", 5000)
            return
        color = QColorDialog.getColor(QColor(*self.image.palette[index]), self,
                                      f"Palette color #{index}")
        if color.isValid():
            self.push_undo()
            self.image.palette[index] = (color.red(), color.green(), color.blue())
            self.changed()

    def import_picture(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import picture", "",
            "Pictures (*.png *.bmp *.gif *.jpg *.jpeg *.ico *.xpm *.ppm);;All files (*)")
        if not path:
            return
        picture = QImage(path)
        if picture.isNull():
            QMessageBox.critical(self, APP_NAME, "Could not read that picture.")
            return
        size = self.image.size
        scaled = picture.convertToFormat(QImage.Format.Format_ARGB32).scaled(
            size, size, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        ox, oy = (size - scaled.width()) // 2, (size - scaled.height()) // 2
        rgba = []
        for y in range(size):
            for x in range(size):
                sx, sy = x - ox, y - oy
                if 0 <= sx < scaled.width() and 0 <= sy < scaled.height():
                    c = scaled.pixelColor(sx, sy)
                    rgba.append((c.red(), c.green(), c.blue(), c.alpha()))
                else:
                    rgba.append((0, 0, 0, 0))
        self.apply(ico.quantize(rgba, size, self.image.palette, dither=self.dither))

    # files

    def maybe_save(self):
        if not self.dirty:
            return True
        answer = QMessageBox.question(
            self, APP_NAME, "Save changes to this icon?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel)
        if answer == QMessageBox.StandardButton.Save:
            return self.save()
        return answer == QMessageBox.StandardButton.Discard

    def _load(self, images, path):
        self.images, self.current, self.path = images, 0, path
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.dirty = False
        self._default_colors()
        self.fit_zoom()
        self.refresh()

    def new_icon(self, confirm=True):
        if confirm and not self.maybe_save():
            return
        self._load([IconImage.blank(32, 4), IconImage.blank(16, 4)], None)

    def open_icon(self, path=None):
        if not self.maybe_save():
            return
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, "Open icon", "", "Icons (*.ico);;All files (*)")
            if not path:
                return
        try:
            images, notes = ico.load_ico(path)
        except (OSError, ico.IcoError) as e:
            QMessageBox.critical(self, APP_NAME, f"Could not open {os.path.basename(path)}:\n\n{e}")
            return
        self._load(images, path)
        if notes:
            QMessageBox.information(self, APP_NAME, "Some images were adjusted:\n\n" + "\n".join(notes))

    def save(self):
        return self._write(self.path) if self.path else self.save_as()

    def save_as(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save icon", self.path or "Untitled.ico",
                                              "Icons (*.ico)")
        if not path:
            return False
        if not path.lower().endswith(".ico"):
            path += ".ico"
        return self._write(path)

    def _write(self, path):
        errors, _ = ico.validate(self.images)
        if errors:
            QMessageBox.critical(self, APP_NAME, "The icon can't be saved yet:\n\n" + "\n".join(errors))
            return False
        try:
            ico.save_ico(path, self.images)
        except (OSError, ico.IcoError) as e:
            QMessageBox.critical(self, APP_NAME, f"Could not save:\n\n{e}")
            return False
        self.path, self.dirty = path, False
        self.refresh()
        self.statusBar().showMessage(f"Saved {path}", 8000)
        return True

    def closeEvent(self, e):
        if self.maybe_save():
            e.accept()
        else:
            e.ignore()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        urls = e.mimeData().urls()
        if urls and urls[0].isLocalFile():
            self.open_icon(urls[0].toLocalFile())


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyle("Windows")
    app.setPalette(retro_palette())
    editor = Editor()
    editor.show()
    if len(sys.argv) > 1:
        editor.open_icon(sys.argv[1])
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
