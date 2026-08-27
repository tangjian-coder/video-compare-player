"""Application stylesheet (QSS), dark flat theme for video work."""

from PySide6.QtWidgets import QApplication

APP_QSS = """
* {
    font-family: "Segoe UI", "Microsoft YaHei UI", sans-serif;
    font-size: 9pt;
}
QMainWindow, QWidget#centralContainer {
    background: #232426;
}
QPushButton {
    background: #3a3d41;
    color: #e6e6e6;
    border: 1px solid #4f5358;
    border-radius: 4px;
    padding: 6px 14px;
    min-width: 48px;
}
QPushButton:hover {
    background: #4a6b8a;
    border-color: #5b9bd5;
}
QPushButton:pressed {
    background: #2f5f8f;
}
QPushButton:disabled {
    background: #2b2d30;
    color: #666a6f;
    border-color: #3a3d41;
}
QPushButton#playButton {
    min-width: 84px;
    font-weight: 600;
    background: #2f5f8f;
    border-color: #5b9bd5;
}
QPushButton#playButton:hover {
    background: #3a70a0;
}
QPushButton#miniButton {
    min-width: 36px;
    padding: 3px 10px;
}
QPushButton#syncButton {
    border-color: #8a6d3b;
}
QPushButton#syncButton:hover {
    background: #6b5636;
    border-color: #c8a24d;
}
QPushButton#alignButton {
    font-weight: 600;
    background: #7a5c24;
    border-color: #c8a24d;
}
QPushButton#alignButton:hover {
    background: #93702f;
}
QLabel#syncChip {
    border-radius: 4px;
    padding: 3px 10px;
    font-family: "Cascadia Mono", Consolas, monospace;
}
QLabel#syncChip[state="off"] {
    background: #5c4322;
    color: #e8c06a;
}
QLabel#syncChip[state="on"] {
    background: #1e4620;
    color: #7ede84;
}
QComboBox {
    background: #3a3d41;
    color: #e6e6e6;
    border: 1px solid #4f5358;
    border-radius: 4px;
    padding: 4px 8px;
}
QComboBox::drop-down {
    border: none;
    width: 18px;
}
QComboBox QAbstractItemView {
    background: #2b2d30;
    color: #e6e6e6;
    selection-background-color: #2f5f8f;
    outline: none;
}
QFrame#videoView {
    background: #17181a;
    border: 1px solid #43464a;
    border-radius: 4px;
}
QLabel#viewBadge {
    background: #2f5f8f;
    color: #ffffff;
    border-radius: 4px;
    padding: 2px 10px;
    font-weight: 700;
}
QLabel#captionLabel {
    color: #9a9da1;
}
QLabel#placeholderLabel {
    color: #5c6166;
    font-size: 13pt;
}
QLabel#timeLabel {
    color: #9a9da1;
    font-family: "Cascadia Mono", Consolas, monospace;
}
QLabel#zoomLabel {
    color: #c8a24d;
    font-family: "Cascadia Mono", Consolas, monospace;
}
QStatusBar {
    background: #232426;
    color: #9a9da1;
}
QSplitter::handle {
    background: #17181a;
}
"""


def apply_app_style(app: QApplication) -> None:
    """Apply the dark flat stylesheet to the application."""
    app.setStyleSheet(APP_QSS)
