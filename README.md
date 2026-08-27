# video-compare-player

双屏视频同步对比播放器。为运动分析场景设计：**HLG/Dolby HDR、120fps 高帧率、高码率原片直接播放**（libmpv 硬件解码），无需预转码。

## 功能（v1）

- 双屏同步对比：同步播放/暂停、逐帧进退、变速（0.1x–4x）、主时间轴拖动
- 同步点校准：在两个视图中各标记"同一物理瞬间"，自动对齐时间轴
- 缩放/平移：滚轮以光标为中心缩放（0.5x–8x）、左键拖拽平移、双击复位
- 拖放加载，支持命令行直接传两个视频

## 运行

```powershell
# 方式 1：脚本（可带视频路径）
.\scripts\run.ps1 left.mp4 right.mp4

# 方式 2：直接运行
.\.venv\Scripts\python.exe -m vcplayer left.mp4 right.mp4
```

## 界面布局

左右两个独立视频块（Kinovea 双屏风格）：每块含标题栏（A/B 徽章 + 文件名）、视频区、底部控制条（本侧逐帧 `<` `>` + 时间读数）。下方是共享传输栏（文件 / 走带 / 速度与同步）和状态栏（A 时间 / offset / 实时漂移 / 帧率）。

**同步用法（一步）**：两个视频拍摄开始时间不同是正常情况。用各块底部的 `<` `>` 把两侧各自走到同一物理瞬间（如秒表刚跳到 `05.00` 的帧）→ 点 **`⇄ Align current frames`** 或按 `S` → chip 变绿显示 offset，时间轴出现金色锚点，完成。按 `R` 清除。

## 快捷键

| 键 | 功能 |
| :--- | :--- |
| `Space` | 同步播放/暂停 |
| `←` / `→` | 双侧同步逐帧后退/前进 |
| `↑` / `↓` | 变速 |
| `Home` / `End` | 跳到开头/结尾 |
| `S` | **一键对齐当前帧**（同步） |
| `R` | 清除同步 |
| `Ctrl+O` | 为当前视图打开视频 |

视频文件直接**拖进左块或右块**即可加载（空块显示拖放提示）。

## 开发

```powershell
.\.venv\Scripts\python.exe -m pytest      # 单测
.\.venv\Scripts\ruff.exe check src tests  # lint
.\.venv\Scripts\mypy.exe                  # 类型检查
.\.venv\Scripts\python.exe scripts\probe_decode.py  # 无头解码验证
```

## 结构

```
src\vcplayer\
├── core\    models.py（数据模型）player.py（libmpv 封装）sync.py（同步引擎，纯逻辑）
├── ui\      video_view.py（渲染表面+缩放平移）timeline.py（时间轴）main_window.py
├── app.py / __main__.py / config.py
tests\       core 逻辑单测（FakePlayer stub，无 GUI 依赖）
vendor\      libmpv-2.dll（mpv v0.41，硬解 + HDR）
docs\        设计文档
```

设计决策见 `docs\2026-08-21-video-compare-player-design.md`。

## 环境要求

- Windows 10/11，Python 3.12+（`.venv` 已建好）
- `vendor\libmpv-2.dll` 必须存在（重新部署时从 mpv-dev 包提取：https://sourceforge.net/projects/mpv-player-windows/files/libmpv/）
