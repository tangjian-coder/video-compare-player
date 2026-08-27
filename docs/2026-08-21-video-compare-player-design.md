# video-compare-player 设计文档

日期：2026-08-21
状态：已批准（用户 "go"）

## 背景

Kinovea 内置 FFmpeg 为 2015 年版（avcodec-56），软解高帧率/HDR/高码率素材时严重卡顿，且无法通过替换 DLL 升级（ABI 不兼容，P/Invoke 绑定 avcodec-56）。本项目用 **libmpv v0.41**（NVDEC 硬解 + HDR tone-mapping + 精确 seek）实现一个双屏同步对比播放器，直接播放原始文件，无需预转码。

## 需求（v1）

- 双屏同步对比播放：同步播放/暂停/逐帧/变速/主时间轴拖动
- 同步点校准：各视图标记同步帧，自动对齐时间轴（offset 模型）
- 缩放/平移：滚轮以光标为中心缩放（0.5x–8x）、拖拽平移、双击复位，两视图独立
- 格式友好：HEVC/H.264、HLG/Dolby HDR、120fps、248Mbps 原片直接播放
- 拖放加载、命令行加载（`python -m vcplayer left.mp4 right.mp4`）

明确不做（v2 候选）：画线/角度标注、测量、KVA 兼容、视频导出。

## 架构

```
MainWindow (PySide6)
├── VideoView A ── PlayerController A ── libmpv #1
├── VideoView B ── PlayerController B ── libmpv #2
├── TimelineWidget（主时间轴，帧刻度）
└── SyncEngine（偏移模型 + 漂移校正，纯逻辑可单测）
```

- `core/models.py`：VideoMeta、SyncState（offset = point_b - point_a）
- `core/player.py`：PlayerController 封装单个 libmpv 实例（wid 嵌入），不含同步逻辑
- `core/sync.py`：SyncEngine 只做时间对齐；主时间轴以 A 为参考，t_b = t_a + offset
- `ui/`：video_view（渲染表面 + 缩放平移鼠标交互）、timeline（自绘标尺）、main_window（装配 + 快捷键 + 状态栏）

## 关键机制

### 同步模型
- `offset = sync_point_b - sync_point_a`，主时间 m → t_a = m，t_b = m + offset
- 有效区间 `master_range = [max(0, -offset), min(dur_a, dur_b - offset)]`

### 播放同步（实测驱动设计，2026-08-21 定稿）
- 播放=两实例自由跑（mpv 原生平滑 + 硬解），drift 由 `SyncEngine.regulate()` 闭环收敛：EWMA 平滑漂移读数（α=0.3）→ 死区 1 帧 → 超出按 `d/0.3` 比例调 B 速（封顶 ±10%）
- **播放中严禁硬 seek**：实测 seek 会重启 B 的管线并引入 ~40ms 新偏斜（"校正义成为噪声源"）
- **速度写入只在变化时**（每次写入约1ms 播放打嗝，重复写会累积出锯齿漂移）
- 暂停/启动偏斜（~40ms 管线唤醒不对称）：暂停后 200ms 由 `_resync_if_needed` 一次性精确吸附（≤1 帧）
- 锁步逐帧驱动（frame-step/exact-seek 追时钟）已实测否决：Qt 事件循环饿死 2ms 定时器（实测 15-200ms），Python 线程 + GIL 竞争同样不达标；纯脚本环境可行但应用内不可行

### 缩放/平移
- mpv `video-zoom` 为 log2 刻度（0 = 1x）；UI 层用线性 z∈[0.5, 8]，转换 `video_zoom = log2(z)`
- 光标锚定：`pan1 = c - (c - pan0) * z1 / z0`（c 为光标相对窗口中心的归一化坐标）

### mpv 选项
`hwdec=auto-safe`、`hr-seek=yes`、`keep-open=yes`、`aid=no`、`osc=no`、禁用 mpv 内建键鼠绑定（Qt 统一接管）。

## 错误处理
- libmpv 缺失/文件打不开 → 错误弹窗 + 日志，不崩溃
- 时长/帧率不同 → 取有效交集区间；超出一侧停末帧（keep-open）

## 测试
- pytest 单测：SyncState/SyncEngine 偏移、区间裁剪、步进、漂移判定（FakePlayer stub，无 GUI）
- 验收：HLG 120fps 原片 + 248Mbps 稳像片双开，播放 5 分钟漂移 < 1 帧

## 环境
- Python 3.12（python.org，user scope）+ `.venv`
- 依赖：PySide6、python-mpv；dev：pytest、ruff、mypy
- `vendor/libmpv-2.dll`（mpv-dev-x86_64-20260301-git-05fac7f）
