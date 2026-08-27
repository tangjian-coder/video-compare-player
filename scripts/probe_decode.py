"""Headless decode verification: probes both videos through libmpv directly."""

import os
import sys
import time
from pathlib import Path

# Make libmpv discoverable before importing mpv.
VENDOR = Path(__file__).resolve().parents[1] / "vendor"
os.environ["PATH"] = str(VENDOR) + os.pathsep + os.environ.get("PATH", "")
os.add_dll_directory(str(VENDOR))

import mpv  # noqa: E402

VIDEOS = [
    r"D:\0806_Q2\0813\HIS_P1dump_V2_20260821_095135\HIS\Preview\20260821_051956\VID_20260821_051958_DOLBY_1080p.mp4",
    r"D:\0806_Q2\0813\HIS_P1dump_V2_20260821_095135\HIS\Preview\20260821_051956\out\final_stabilized.mp4",
]


def probe(path: str) -> None:
    """Decode-probe one file: metadata, hwdec, exact seek roundtrip."""
    m = mpv.MPV(vo="null", ao="null", hwdec="auto-safe", hr_seek=True, keep_open=True, pause=True)
    loaded = False

    @m.event_callback("file-loaded")
    def _loaded(_e) -> None:
        nonlocal loaded
        loaded = True

    m.command("loadfile", path)
    deadline = time.time() + 10
    while not loaded and time.time() < deadline:
        time.sleep(0.05)
    if not loaded:
        print(f"FAIL {Path(path).name}: load timeout")
        m.terminate()
        return

    time.sleep(1.0)  # let a few frames decode so hwdec engages
    params = m.video_params or {}
    fps = getattr(m, "container_fps", None)
    print(f"--- {Path(path).name}")
    print(f"  duration={m.duration:.3f}s fps={fps} size={params.get('w')}x{params.get('h')}")
    print(f"  video-codec={m.video_codec} hwdec-current={m.hwdec_current}")

    # Exact seek roundtrip to mid-file and frame stepping.
    target = round(m.duration / 2, 3)
    m.command("seek", target, "absolute", "exact")
    time.sleep(0.5)
    pos = m.time_pos
    print(f"  seek exact -> target={target}, actual={pos}, ok={abs(pos - target) < 2 / fps}")
    m.command("frame-step")
    time.sleep(0.3)
    print(f"  frame-step: {pos} -> {m.time_pos}")
    m.terminate()


for v in VIDEOS:
    probe(v)
print("probe done")
sys.exit(0)
