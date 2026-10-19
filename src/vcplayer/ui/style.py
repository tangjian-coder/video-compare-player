"""Application stylesheet (QSS): macOS dark-mode inspired theme.

Palette follows Apple's dark-mode system colors (HIG dark variants):
    window bg          #1C1C1E / panel #2C2C2E / card #161618
    system blue        #0A84FF   (primary action, view A)
    system green       #30D158   (view B, "synced" state)
    indigo             #5E5CE6   (align action)
    system orange      #FF9F0A   (zoom readout, view D)
    system yellow      #FFD60A   (sync anchor marker)
    system red         #FF453A   (record / playhead)
"""

from PySide6.QtWidgets import QApplication

from .theme import ON_LIGHT_TEXT, VIEW_COLORS

APP_QSS = """
* {
    font-family: "Inter", "Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI",
                 sans-serif;
    font-size: 10pt;
    color: #f5f5f7;
}
QMainWindow, QWidget#centralContainer {
    background: #1c1c1e;
}
QToolTip {
    background: #2c2c2e;
    color: #f5f5f7;
    border: 1px solid rgba(255, 255, 255, 26);
    border-radius: 6px;
    padding: 4px 8px;
    font-size: 9pt;
}

/* -- buttons ------------------------------------------------------------- */
QPushButton, QToolButton {
    background: #2c2c2e;
    color: #f5f5f7;
    border: 1px solid rgba(255, 255, 255, 18);
    border-radius: 7px;
    padding: 0 12px;
    min-height: 28px;
}
QPushButton:hover, QToolButton:hover {
    background: #3a3a3c;
}
QPushButton:pressed, QToolButton:pressed {
    background: #48484a;
}
QPushButton:disabled, QToolButton:disabled {
    background: #232326;
    color: #6e6e73;
    border-color: rgba(255, 255, 255, 10);
}
QToolButton#toolButton {
    padding: 0 10px;
}
QToolButton#toolButton::menu-indicator {
    image: none;
}
QPushButton#playButton {
    background: #0a84ff;
    border: none;
    border-radius: 8px;
    min-width: 54px;
    min-height: 30px;
    padding: 0;
}
QPushButton#playButton:hover {
    background: #2f95ff;
}
QPushButton#playButton:pressed {
    background: #0071e3;
}
QPushButton#playButton:disabled {
    background: #3a3a3c;
}
QPushButton#miniButton {
    background: transparent;
    border: none;
    border-radius: 6px;
    min-width: 26px;
    max-width: 26px;
    min-height: 26px;
    max-height: 26px;
    padding: 0;
}
QPushButton#miniButton:hover {
    background: rgba(255, 255, 255, 20);
}
QPushButton#miniButton:pressed {
    background: rgba(255, 255, 255, 36);
}
QPushButton#iconButton {
    background: transparent;
    border: none;
    padding: 0;
    min-width: 30px;
    max-width: 30px;
    min-height: 28px;
    max-height: 28px;
}
QPushButton#iconButton:hover {
    background: rgba(255, 255, 255, 20);
}
QPushButton#iconButton:pressed {
    background: rgba(255, 255, 255, 36);
}
QPushButton#iconButton:disabled {
    background: transparent;
}
QPushButton#alignButton {
    background: rgba(94, 92, 230, 46);
    border: 1px solid rgba(94, 92, 230, 115);
    color: #a9a7ff;
    font-weight: 600;
}
QPushButton#alignButton:hover {
    background: rgba(94, 92, 230, 71);
}
QPushButton#alignButton:pressed {
    background: rgba(94, 92, 230, 92);
}
QPushButton#syncButton {
    background: transparent;
    border: 1px solid rgba(255, 255, 255, 20);
    color: #a1a1a6;
}
QPushButton#syncButton:hover {
    background: #2c2c2e;
    color: #f5f5f7;
}
QPushButton#recButton {
    background: transparent;
    border: none;
    color: #ff453a;
    font-weight: 600;
    padding: 0 10px;
}
QPushButton#recButton:hover {
    background: rgba(255, 69, 58, 31);
}
QPushButton#recButton[rec="on"] {
    background: #ff453a;
    color: #ffffff;
}
QPushButton#recButton[rec="on"]:hover {
    background: #ff6961;
}
QPushButton#crosshairButton {
    background: transparent;
    border: none;
    color: #ff9f0a;
    padding: 0;
    min-width: 30px;
    max-width: 30px;
    min-height: 28px;
    max-height: 28px;
    border-radius: 6px;
}
QPushButton#crosshairButton:hover {
    background: rgba(255, 159, 10, 31);
}
QPushButton#crosshairButton:checked {
    background: rgba(255, 159, 10, 46);
}

/* -- inputs -------------------------------------------------------------- */
QComboBox {
    background: #2c2c2e;
    color: #f5f5f7;
    border: 1px solid rgba(255, 255, 255, 18);
    border-radius: 7px;
    padding: 0 8px;
    min-height: 28px;
    min-width: 64px;
}
QComboBox:hover {
    background: #3a3a3c;
}
QComboBox::drop-down {
    border: none;
    width: 22px;
}
QComboBox::down-arrow {
    image: none;
}
QComboBox QAbstractItemView {
    background: #2c2c2e;
    color: #f5f5f7;
    selection-background-color: #0a84ff;
    outline: none;
    border: 1px solid rgba(255, 255, 255, 20);
    border-radius: 8px;
    padding: 4px;
}

/* -- menus, dialogs, scrollbars ----------------------------------------- */
QMenu {
    background: #2c2c2e;
    border: 1px solid rgba(255, 255, 255, 23);
    border-radius: 8px;
    padding: 5px;
}
QMenu::item {
    padding: 6px 26px 6px 12px;
    border-radius: 5px;
    color: #f5f5f7;
}
QMenu::item:selected {
    background: #0a84ff;
    color: #ffffff;
}
QMenu::item:disabled {
    color: #6e6e73;
}
QMenu::separator {
    height: 1px;
    background: rgba(255, 255, 255, 20);
    margin: 5px 8px;
}
QMessageBox {
    background: #2c2c2e;
}
QMessageBox QLabel {
    color: #f5f5f7;
}
QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 2px;
}
QScrollBar::handle:vertical {
    background: rgba(255, 255, 255, 46);
    border-radius: 4px;
    min-height: 28px;
}
QScrollBar::handle:vertical:hover {
    background: rgba(255, 255, 255, 76);
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: transparent;
}
QScrollBar:horizontal {
    background: transparent;
    height: 10px;
    margin: 2px;
}
QScrollBar::handle:horizontal {
    background: rgba(255, 255, 255, 46);
    border-radius: 4px;
    min-width: 28px;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
}

/* -- panes --------------------------------------------------------------- */
QFrame#videoView {
    background: #161618;
    border: 2px solid #242427;
    border-radius: 10px;
}
QFrame#videoView[activePane="true"] {
    border-color: rgba(10, 132, 255, 90);
}
QFrame#videoView[trackHover="true"] {
    border-color: rgba(255, 255, 255, 105);
}
QFrame#videoView[dragSource="true"] {
    border: 2px dashed #6e6e73;
}
QFrame#videoView[dropZone="left"] {
    border-left-color: #0a84ff;
}
QFrame#videoView[dropZone="right"] {
    border-right-color: #0a84ff;
}
QFrame#videoView[dropZone="top"] {
    border-top-color: #0a84ff;
}
QFrame#videoView[dropZone="bottom"] {
    border-bottom-color: #0a84ff;
}
QFrame#videoView[dropZone="center"] {
    border-color: #0a84ff;
}
QLabel#viewBadgeA, QLabel#viewBadgeB, QLabel#viewBadgeC, QLabel#viewBadgeD {
    color: #ffffff;
    border-radius: 6px;
    font-weight: 700;
    font-size: 9pt;
}
QLabel#viewBadgeA {
    background: $VIEW_A;
}
QLabel#viewBadgeB {
    background: $VIEW_B;
    color: $ON_LIGHT_TEXT;
}
QLabel#viewBadgeC {
    background: $VIEW_C;
}
QLabel#viewBadgeD {
    background: $VIEW_D;
    color: $ON_LIGHT_TEXT;
}
QLabel#captionLabel {
    color: #c7c7cc;
    font-size: 9.5pt;
}
QLabel#headerGrip {
    color: #5c5c60;
    font-size: 10pt;
    padding: 0 2px;
}
QLabel#placeholderTitle {
    color: #8e8e93;
    font-size: 12pt;
    font-weight: 500;
}
QLabel#placeholderHint {
    color: #5c6166;
    font-size: 9pt;
}
QLabel#placeholderLabel {
    color: #5c6166;
    font-size: 12pt;
}
QLabel#timeLabel {
    color: #8e8e93;
    font-family: "Cascadia Mono", Consolas, monospace;
    font-size: 9pt;
}
QLabel#zoomLabel {
    color: #ff9f0a;
    font-family: "Cascadia Mono", Consolas, monospace;
    font-size: 9pt;
}
QLabel#rotationLabel {
    color: #8e8e93;
    font-family: "Cascadia Mono", Consolas, monospace;
    font-size: 9pt;
}
QWidget#timeline {
    background: transparent;
}

/* -- chrome -------------------------------------------------------------- */
QFrame#transportBar {
    background: #1a1a1c;
    border-top: 1px solid rgba(255, 255, 255, 15);
}
QFrame#toolbarSeparator {
    background: rgba(255, 255, 255, 20);
    min-width: 1px;
    max-width: 1px;
}
QLabel#syncChip {
    border-radius: 6px;
    padding: 3px 9px;
    font-family: "Cascadia Mono", Consolas, monospace;
    font-size: 9pt;
}
QLabel#syncChip[state="off"] {
    background: rgba(255, 159, 10, 31);
    color: #ffb340;
}
QLabel#syncChip[state="on"] {
    background: rgba(48, 209, 88, 36);
    color: #4cd964;
}
QStatusBar {
    background: #1c1c1e;
    border-top: 1px solid rgba(255, 255, 255, 15);
    color: #8e8e93;
    min-height: 26px;
}
QStatusBar::item {
    border: none;
}
QStatusBar QLabel {
    color: #8e8e93;
}
QLabel#statusLive {
    color: #8e8e93;
    font-family: "Cascadia Mono", Consolas, monospace;
    font-size: 9pt;
}
"""


def apply_app_style(app: QApplication) -> None:
    """Apply the dark stylesheet on top of a predictable Fusion base style."""
    qss = APP_QSS.replace("$VIEW_A", VIEW_COLORS["a"])
    qss = qss.replace("$VIEW_B", VIEW_COLORS["b"])
    qss = qss.replace("$VIEW_C", VIEW_COLORS["c"])
    qss = qss.replace("$VIEW_D", VIEW_COLORS["d"])
    qss = qss.replace("$ON_LIGHT_TEXT", ON_LIGHT_TEXT)
    app.setStyle("Fusion")
    app.setStyleSheet(qss)
