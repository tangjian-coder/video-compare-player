# video-compare-player

多视图视频同步对比播放器（2–4 视图动态切换）。为运动分析场景设计：**HLG/Dolby HDR、120fps 高帧率、高码率原片直接播放**（libmpv 硬件解码），无需预转码。

## 功能（v1）

- **2–4 视图同步对比**：默认双视图，`＋` / `V` 键追加、`－` 移除（最多 4 块）；同步播放/暂停、逐帧进退、变速（0.1x–4x）、主时间轴拖动全视图生效
- **拖动标题栏自由排列窗格**：拖到另一窗格边缘＝插入该行/新行，拖到中心＝交换；4 视图默认 2×2，标题栏右键可选 1×4 / 2×2 / 3+1 等预设，也可重置布局
- 同步点校准：在各视图中标记"同一物理瞬间"（A 为主时间轴，每个跟随视图与 A 各一对偏移），一键对齐全部
- 缩放/平移/旋转：默认操作**所有视图联动**（滚轮缩放各走一档、拖拽像素级 1:1 平移、双击复位），**Ctrl+操作仅作用于本侧**；每侧 `↻` 按钮顺时针旋转 90°
- **一键录屏**：`F9` 把所有视频区（含标题栏与各侧控制条，不含时间轴/走带栏/状态栏）录成 mp4，对齐后同步播放的演示素材直接可发（自动选档：NVENC 硬编 60fps → 桌面复制+x264 → gdigrab+x264）
- **十字准星**：`C` 在每个视图**窗格中心**显示一条 **1px 红色十字线**（mpv OSD 渲染），缩放/平移/旋转时**纹丝不动**——这是对比运动幅度的稳定参照物，会被录进录屏
- 拖放加载，支持命令行直接传 2–4 个视频

## 运行

```powershell
# 方式 1：脚本（可带 2–4 个视频路径）
.\scripts\run.ps1 a.mp4 b.mp4            # 2 视图
.\scripts\run.ps1 a.mp4 b.mp4 c.mp4 d.mp4  # 直接 4 视图

# 方式 2：直接运行
.\.venv\Scripts\python.exe -m vcplayer a.mp4 b.mp4
```

## 打包（单文件 exe）

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1
```

产出 `dist\vcplayer.exe`（约 130 MB，已内嵌 libmpv 与图标），发给别人**免安装直接双击**（Windows 10 1809+）。配置与日志写入 `%LOCALAPPDATA%\vcplayer\`。注意：exe 未签名，个别杀软可能误报需手动放行；单文件模式首启需解压，约 5–10 秒。

## 界面布局

窗格排列：2 视图默认并排，3 视图一行，4 视图默认 **2×2**。**拖动任意窗格标题栏**到另一窗格边缘（左/右＝同行插入，上/下＝新行）或中心（交换）即可重排；标题栏右键有布局预设与"重置布局"。视图身份（A/B/C/D、主时间轴）不随位置变化。

2–4 个独立视频块（Kinovea 双屏风格）：每块含标题栏（A/B/C/D 徽章 + 文件名，蓝/绿/靛蓝/橙）、视频区、底部控制条（本侧逐帧 `<` `>` + 时间读数）。下方是共享传输栏（文件与视图 ＋－ / 走带 / 速度与同步）和状态栏（A 时间 / 各对 offset / 实时漂移 / 帧率）。**`V` 键或 `＋` 追加视图，`－` 移除最后一个（A、B 常驻）**。

**同步校准**：用各块底部的 `<` `>` 把每个视图各自走到同一物理瞬间（如秒表刚跳到 `05.00` 的帧）→ 点 `⇄ Align current frames` 或按 `S` → 完成。按 `R` 清除全部。详见操作手册。

## 快捷键

`Space` 同步播放/暂停 · `←`/`→` 全视图逐帧 · `↑`/`↓` 变速 · `Home`/`End` 跳对齐区间首尾 · `S` 一键对齐 · `R` 清除同步 · `F9` 录屏开始/停止 · `C` 十字准星 · `V` 追加视图 · `Ctrl+O` 打开视频

完整快捷键、时间轴与状态栏的详细说明见 **`docs\controls-guide.md`（操作手册）**。

视频文件直接**拖进任意视频块**即可加载（空块显示拖放提示）。

## 鼠标手势（视频区）

| 手势 | 默认（所有视图联动） | Ctrl（仅本侧） |
| :--- | :--- | :--- |
| 滚轮 | 缩放：各自在档位上前进/后退一档（1.0–8.0，保留各侧倍率差） | 本侧缩放 |
| 左键拖拽 | 平移：像素级 1:1 跟随鼠标，各自边缘钳制 | 本侧平移 |
| 双击 | 复位缩放、平移和旋转 | 本侧复位 |

每块底部 Play 旁的 `↻` 按钮＝本侧顺时针旋转 90°（循环 0→90→180→270，旋转时平移回中、缩放保留），当前角度直接显示在按钮上（如 `↻ 90°`）；缩放倍率 `×2.4` 显示在时间读数旁。

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
├── core\    models.py（数据模型）player.py（libmpv 封装）sync.py（同步引擎，纯逻辑，每跟随视图一个实例）
├── ui\      video_view.py（渲染表面+缩放平移）timeline.py（多轨时间轴）main_window.py
├── app.py / __main__.py / config.py
tests\       core 逻辑单测（FakePlayer stub，无 GUI 依赖）
vendor\      libmpv-2.dll（mpv v0.41，硬解 + HDR）
docs\        设计文档
```

设计决策见 `docs\2026-08-21-video-compare-player-design.md`。

## 环境要求

- Windows 10/11，Python 3.12+（`.venv` 已建好）
- `vendor\libmpv-2.dll` 必须存在（重新部署时从 mpv-dev 包提取：https://sourceforge.net/projects/mpv-player-windows/files/libmpv/）
