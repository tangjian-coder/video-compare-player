"""Application stylesheet (QSS): macOS dark-mode theme.

Palette follows Apple's dark-mode system colors (HFSA dark variants):
    background layers  #1E1E20 / #2C2C2E / #161618
    system blue        #0A84FF   (primary action, view A)
    system green       #30D158   (view B, "synced" state)
    indigo             #5E5CE6   (align action)
    system orange      #FF9F0A   (zoom readout)
    system yellow      #FFD60A   (sync anchor marker)
    system red         #FF453A   (playhead)
"""

from PySide6.QtWidgets import QApplication

APP_QSS = """
* {
    font-family: "Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI", sans-serif;
    font-size: 9pt;
}
QMainWindow, QWidget#centralContainer {
    background: #1e1e20;
}
QPushButton {
    background: #2c2c2e;
    color: #f2f2f7;
    border: none;
    border-radius: 6px;
    padding: 5px 14px;
    min-width: 48px;
}
QPushButton:hover {
    background: #48484a;
}
QPushButton:pressed {
    background: #3a3a3c;
}
QPushButton:disabled {
    background: #3a3a3c;
    color: #636366;
}
QPushButton#playButton {
    min-width: 84px;
    font-weight: 600;
    background: #0a84ff;
    color: #ffffff;
}
QPushButton#playButton:hover {
    background: #409cff;
}
QPushButton#playButton:pressed {
    background: #0071e3;
}
QPushButton#playButton:disabled {
    background: #3a3a3c;
    color: #636366;
}
QPushButton#miniButton {
    min-width: 36px;
    padding: 3px 10px;
}
QPushButton#miniButton:hover {
    background: #48484a;
}
QPushButton#alignButton {
    font-weight: 600;
    background: #5e5ce6;
    color: #ffffff;
}
QPushButton#alignButton:hover {
    background: #7a78e8;
}
QPushButton#alignButton:pressed {
    background: #4c4ad0;
}
QPushButton#syncButton {
    background: #2c2c2e;
    color: #f2f2f7;
}
QPushButton#syncButton:hover {
    background: #48484a;
}
QPushButton#recButton {
    color: #ff453a;
    font-weight: 600;
}
QPushButton#recButton:hover {
    background: #48484a;
}
QPushButton#recButton[rec="on"] {
    background: #ff453a;
    color: #ffffff;
}
QPushButton#recButton[rec="on"]:hover {
    background: #ff6961;
}
QPushButton#crosshairButton {
    color: #ff9f0a;
    font-weight: 600;
}
QPushButton#crosshairButton:hover {
    background: #48484a;
}
QPushButton#crosshairButton:checked {
    background: #ff9f0a;
    color: #1e1e20;
}
QPushButton#crosshairButton:checked:hover {
    background: #ffb340;
}
QComboBox {
    background: #2c2c2e;
    color: #f2f2f7;
    border: none;
    border-radius: 6px;
    padding: 5px 10px;
}
QComboBox:hover {
    background: #48484a;
}
QComboBox::drop-down {
    border: none;
    width: 18px;
}
QComboBox QAbstractItemView {
    background: #2c2c2e;
    color: #f2f2f7;
    selection-background-color: #0a84ff;
    outline: none;
    border-radius: 6px;
}
QLabel#syncChip {
    border-radius: 7px;
    padding: 3px 10px;
    font-family: "Cascadia Mono", Consolas, monospace;
}
QLabel#syncChip[state="off"] {
    background: #3a2a12;
    color: #ff9f0a;
}
QLabel#syncChip[state="on"] {
    background: #1e4620;
    color: #30d158;
}
QFrame#videoView {
    background: #161618;
    border: 1px solid #2c2c2e;
    border-radius: 8px;
}
QLabel#viewBadge {
    color: #ffffff;
    border-radius: 7px;
    padding: 2px 10px;
    font-weight: 700;
}
QLabel#viewBadgeA {
    background: #0a84ff;
}
QLabel#viewBadgeB {
    background: #30d158;
}
QLabel#captionLabel {
    color: #98989d;
}
QLabel#placeholderLabel {
    color: #5c6166;
    font-size: 13pt;
}
QLabel#timeLabel {
    color: #98989d;
    font-family: "Cascadia Mono", Consolas, monospace;
}
QLabel#zoomLabel {
    color: #ff9f0a;
    font-family: "Cascadia Mono", Consolas, monospace;
}
QStatusBar {
    background: #1e1e20;
    color: #98989d;
}
QStatusBar QLabel {
    color: #98989d;
}
QSplitter::handle {
    background: #1e1e20;
}
QToolTip {
    background: #2c2c2e;
    color: #f2f2f7;
    border: 1px solid #48484a;
    border-radius: 5px;
    padding: 4px 8px;
}
"""


def apply_app_style(app: QApplication) -> None:
    """Apply the macOS dark stylesheet to the application."""
    app.setStyleSheet(APP_QSS)
