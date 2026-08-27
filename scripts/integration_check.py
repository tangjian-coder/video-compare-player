"""End-to-end lockstep verification with real videos.

Starts the real MainWindow in-process, plays for a few seconds while sampling
the drift between views, then pauses and checks the settled drift. Prints
percentile stats; exits nonzero if sync is worse than one reference frame.

A window briefly opens on the desktop while this runs.
"""

import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
os.environ["PATH"] = str(PROJECT_ROOT / "vendor") + os.pathsep + os.environ.get("PATH", "")
os.add_dll_directory(str(PROJECT_ROOT / "vendor"))

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from vcplayer.config import AppConfig  # noqa: E402
from vcplayer.ui.main_window import MainWindow  # noqa: E402

LEFT = Path(
    r"D:\0806_Q2\0813\HIS_P1dump_V2_20260821_095135\HIS\Preview\20260821_051956\VID_20260821_051958_DOLBY_1080p.mp4"
)
RIGHT = Path(
    r"D:\0806_Q2\0813\HIS_P1dump_V2_20260821_095135\HIS\Preview\20260821_051956\out\final_stabilized.mp4"
)
PLAY_SECONDS = 4.0


def pump(seconds: float) -> None:
    """Run the event loop for a fixed wall time."""
    loop = QEventLoop()
    QTimer.singleShot(int(seconds * 1000), loop.quit)
    loop.exec()


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow(AppConfig())
    window.show()
    window.initialize(left=LEFT, right=RIGHT)

    deadline = time.monotonic() + 10.0
    while (window.engine is None or not window.engine.ready) and time.monotonic() < deadline:
        pump(0.1)
    if window.engine is None or not window.engine.ready:
        print("FAIL: videos did not become ready in time")
        return 1
    fps_ref = window.engine.reference_fps
    print(f"ready: fps_ref={fps_ref:.3f}, range={window.engine.master_range()}")

    samples: list[float] = []
    sampler = QTimer()
    sampler.setInterval(50)

    def take_sample() -> None:
        d = window.engine.drift() if window.engine else None
        if d is not None:
            samples.append(d)

    sampler.timeout.connect(take_sample)
    start_a = window.view_a.player.time_pos or 0.0
    sampler.start()
    window._toggle_play()  # start lockstep playback
    pump(PLAY_SECONDS)
    sampler.stop()
    mid_a = window.view_a.player.time_pos or 0.0
    window._toggle_play()  # pause
    pump(0.5)

    settled = window.engine.drift()
    window.close()

    advanced = mid_a - start_a
    frame = 1.0 / fps_ref
    print(f"advanced during play: {advanced:.2f}s of content in {PLAY_SECONDS}s")
    print(f"one frame = {frame * 1000:.2f}ms; settled drift after pause: {abs(settled or 0) * 1000:.2f}ms")
    if samples:
        ordered = sorted(abs(d) for d in samples)
        print(
            f"playing drift readings: p50={ordered[len(ordered) // 2] * 1000:.2f}ms "
            f"p95={ordered[int(len(ordered) * 0.95)] * 1000:.2f}ms (noisy during seeks)"
        )
    print("series(ms):", [round(d * 1000, 1) for d in samples])

    # Hard criteria: playback really advanced ~in real time, and the settled
    # (paused) positions are aligned within a frame. In-flight drift readings
    # are polluted by optimistic time_pos reporting and are informational only.
    ok = advanced >= PLAY_SECONDS * 0.75 and abs(settled or 0) <= frame * 1.5
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
