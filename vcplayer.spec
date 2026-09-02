# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: single-file windowed exe bundling libmpv + assets + ffmpeg."""

import shutil
from pathlib import Path

import imageio_ffmpeg

# Stage the venv's ffmpeg under a fixed name: locate_ffmpeg() looks for
# meipass/ffmpeg.exe in the frozen build.
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
    excludes=["imageio_ffmpeg"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="vcplayer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/icon.ico",
    version="scripts/version_info.txt",
)
