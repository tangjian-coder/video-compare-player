# video-compare-player

Windows 上的多路视频对比播放器。最多同时打开 4 个视频，支持逐帧对齐、同步播放、缩放平移和录屏，适合运动分析和多版本画面对比。底层使用 libmpv 与 PySide6。

## 运行

需要 Windows 10 1809 或更新版本、Python 3.12+。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

本项目依赖 `vendor\libmpv-2.dll`，该文件不包含在 Git 仓库中。请从 [mpv Windows builds](https://sourceforge.net/projects/mpv-player-windows/files/libmpv/) 下载开发包，将 DLL 放到 `vendor\` 目录。

```powershell
# 不带视频启动
.\scripts\run.ps1

# 启动时加载 1–4 个视频
.\scripts\run.ps1 a.mp4 b.mp4
.\scripts\run.ps1 a.mp4 b.mp4 c.mp4 d.mp4
```

也可以直接运行：

```powershell
.\.venv\Scripts\python.exe -m vcplayer a.mp4 b.mp4
```

## 快速上手

1. 把视频拖进窗格。一次拖入多个文件会自动补足视图，最多 4 个。
2. 用每个窗格底部的 `<` / `>` 找到同一物理瞬间，按 `S` 对齐。
3. 按 `Space` 同步播放；点击或拖动时间轴跳转。

未对齐时，各视图只是同时起播，不建立时间偏移。对齐后，播放、逐帧和 seek 都按同步偏移执行。详细操作见 [操作手册](docs/controls-guide.md)。

## 主要功能

- **1–4 路视频对比**：窗格可增删、交换和自由排列，提供并排、堆叠、2×2 等布局。
- **同步与逐帧**：为每个视图设置同步锚点，播放时显示偏移和漂移；支持逐帧、变速、快速预览 seek 和松手精确 seek。
- **多轨时间轴**：每个视图一条轨道，黄色菱形标记同步锚点；未同步时可跳到长视频任意位置，短视频超出自身时长时停在末尾。
- **缩放、平移和旋转**：默认联动所有视图，按住 `Ctrl` 只操作当前视图；支持竖屏视频自动转横屏，包含手机 rotation metadata。
- **录屏和准星**：`F9` 把视频区域录成 MP4；`C` 显示固定在窗格中心的十字准星。
- **原片播放**：支持高码率、高帧率和 HDR 视频，硬解效果取决于显卡、驱动和编码格式。
- **其他**：批量拖入、复制文件名、异步加载、源文件被移动或删除时暂停并标记。

## 常用操作

| 按键 | 功能 |
| --- | --- |
| `Space` | 播放 / 暂停 |
| `←` / `→` | 所有视图逐帧后退 / 前进 |
| `↑` / `↓` | 调整播放速度 |
| `Home` / `End` | 跳到对齐区间首尾 |
| `S` | 对齐各视图当前帧 |
| `R` | 清除同步 |
| `V` | 追加视图 |
| `F9` | 开始 / 停止录屏 |
| `C` | 切换十字准星 |
| `Ctrl+O` | 为当前活动视图打开视频 |

| 鼠标操作 | 默认 | 按住 `Ctrl` |
| --- | --- | --- |
| 滚轮 | 所有视图联动缩放 | 只缩放当前视图 |
| 左键拖拽 | 所有视图联动平移 | 只平移当前视图 |
| 双击 | 所有视图复位 | 只复位当前视图 |

拖动窗格标题栏可以重排；标题栏右侧可复制文件名或清空视频。完整快捷键、布局和录屏说明见 [操作手册](docs/controls-guide.md)。

## 打包 Windows exe

```powershell
# 单文件版
powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1

# 文件夹版
powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1 -OneDir
```

默认输出 `dist\vcplayer.exe`，文件夹版输出到 `dist\vcplayer\`。打包会内嵌 libmpv、ffmpeg 和图标。

单文件版启动时需要先解压，冷启动时间受磁盘和杀毒软件影响。exe 未签名，Windows 可能弹出安全提示。配置和日志写入 `%LOCALAPPDATA%\vcplayer\`。

## 开发

```powershell
# 安装开发依赖
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"

# 测试与静态检查
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m mypy src

# 输出交互性能日志
.\.venv\Scripts\python.exe -m vcplayer --perf
```

界面和同步行为以 [操作手册](docs/controls-guide.md) 为准；早期设计背景见 [设计文档](docs/2026-08-21-video-compare-player-design.md)。
