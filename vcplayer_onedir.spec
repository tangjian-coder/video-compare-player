# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: optional folder build (onefile remains the default)."""

import shutil
from pathlib import Path

import imageio_ffmpeg

_ffmpeg_src = Path(imageio_ffmpeg.get_ffmpeg_exe())
_ffmpeg_staged = Path(SPECPATH) / "build" / "ffmpeg.exe"
_ffmpeg_staged.parent.mkdir(parents=True, exist_ok=True)
shutil.copy2(_ffmpeg_src, _ffmpeg_staged)

a = Analysis(
    ["scripts/launcher.py"],
    pathex=["src"],
    binaries=[
        ("vendor/libmpv-2.dll", "vendor"),
        (str(_ffmpeg_staged), "."),
    ],
    datas=[("assets", "assets")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "imageio_ffmpeg",
        "PIL",
        "PySide6.QtNetwork",
        "PySide6.QtPdf",
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.QtSvg",
        "PySide6.QtVirtualKeyboard",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="vcplayer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/icon.ico",
    version="scripts/version_info.txt",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="vcplayer",
)
