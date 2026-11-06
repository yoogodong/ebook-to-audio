# ebook-to-audio

[English](README.md) | **简体中文**

把电子书文本转换成语音（MP3 / M4B），使用微软 Edge TTS（[`edge-tts`](https://github.com/rany2/edge-tts) 库，免费、无需 API Key）。

## 目前支持

- **默认输出 M4B；默认中文播音员为云健（`zh-CN-YunjianNeural`）**
- `.txt` → `.m4b` / `.mp3` / 两者，自动按句切分，每块不超过 900 字
- `.epub` → 按章节输出 M4B + 章节 MP3；可选合并 MP3
- EPUB 解析使用标准库：spine 阅读顺序、EPUB3 nav / EPUB2 NCX 目录、TOC 锚点细切、h1-h6 兜底
- 分块合成，单块失败自动重试，可配置并发数
- 断点续传：中间块缓存在 `<输出文件>.parts/`；EPUB 另用 `manifest.json` 按章跳过已完成内容
- 合并为单个 MP3；全部成功后自动清理缓存，失败或取消时保留缓存
- M4B 输出：AAC 音频、章节标记、书名/作者/流派元数据、可选封面（需要系统 `ffmpeg` / `ffprobe`）
- 命令行 + PyQt5 图形界面（拖放、选音色、试听、进度、取消）
- 可调音色、语速、音量、音调

## 环境

使用 uv 管理环境：

```bash
uv sync
```

M4B 输出还需要系统 `ffmpeg` / `ffprobe`：

```bash
brew install ffmpeg          # macOS
sudo apt install ffmpeg      # Debian/Ubuntu
```

## 用法

### 命令行

```bash
# 转换 a.txt，默认输出 a.m4b（默认音色：云健）
uv run ebook-to-audio a.txt

# 指定音色、语速、输出路径
uv run ebook-to-audio a.txt -v zh-CN-XiaoxiaoNeural --rate=-10% -o out.m4b

# 只输出 MP3
uv run ebook-to-audio a.txt -f mp3

# 同时输出 MP3 和 M4B
uv run ebook-to-audio a.txt -f both -o book

# 提高并发请求数（默认 3，范围 1-8；过高可能被服务端限流）
uv run ebook-to-audio a.txt -j 4

# 不传文件时交互输入
uv run ebook-to-audio
```

`-o` 给的是输出主名，实际文件按格式生成 `<主名>.mp3` / `<主名>.m4b`。M4B 模式下默认保留中间 MP3；加 `--no-mp3` 会在导出成功后删除它。

### EPUB

```bash
# 默认输出：book.m4b + book_chapters/（默认音色：云健）
uv run ebook-to-audio book.epub

# 同时输出 MP3 与 M4B
uv run ebook-to-audio book.epub -f both -o out/book

# 只输出 MP3
uv run ebook-to-audio book.epub -f mp3

# 只按目录第二层切章
uv run ebook-to-audio book.epub --chapter-level 2

# 超长章节按 2 万字再切，并在每章开头朗读标题
uv run ebook-to-audio book.epub --max-chapter-chars 20000 --read-title

# 导出完成后删除章节 MP3，只留全书文件
uv run ebook-to-audio book.epub --no-chapters
```

EPUB 的输出：

- `<主名>_chapters/001_章节标题.mp3`：每章一个音频，按 spine/TOC 顺序编号
- `<主名>_chapters/manifest.json`：源文件哈希、音色参数、章节文本哈希与状态，重跑时跳过未变化的章节
- `<主名>.mp3`：全书合并（`-f mp3` 或 `-f both` 时生成）
- `<主名>.m4b`：默认输出，带章节标记、书名/作者/流派元数据和封面

M4B 是默认格式；没有安装 `ffmpeg` 时会报错，可用 `-f mp3` 只输出 MP3。

章节归属以 spine（阅读顺序）为准，TOC 只提供标题和层级；目录缺失时回退到 h1-h6 或整文件一章。过短、几乎无文字的章节会被跳过；DRM 加密、固定版式或纯图片章节会被跳过/报错，不做 OCR。

也可以使用 `uv run python -m ebook_to_audio.main a.txt` 或 `scripts/run.sh a.txt`。

### 图形界面

```bash
uv run ebook-to-audio-gui
# 或
scripts/run_gui.sh
```

拖入 `.txt` 或 `.epub`，选择音色、输出格式和输出路径后点“开始转化”；转化中可点“取消”，已完成的块/章节会保留以便续传。M4B 格式需要本机安装 `ffmpeg`。

## 目录结构

```text
scripts/                 # 启动脚本
src/ebook_to_audio/
  main.py                    # 命令行入口（txt / epub 分派）
  core/tts_processor.py      # 文本切分、Edge TTS 合成、缓存续传、并发
  core/converter.py          # txt 的输出格式编排
  core/audio_export.py       # M4B 导出：AAC、章节标记、元数据、封面
  core/epub_processor.py     # EPUB 解析、章节规划、正文清洗
  core/epub_converter.py     # EPUB 逐章 TTS、合并、M4B、manifest
  gui/app.py                 # PyQt5 界面
  gui/icon.py                # 运行时绘制的窗口图标
  utils/file_handler.py      # 文件读写与输出名推导
tests/                       # unittest 用例（默认不联网）
```

## 测试

```bash
uv run python -m unittest discover -s tests -v
```

M4B 测试会在本机生成静音音频并用 `ffmpeg` 校验章节/元数据；没有 `ffmpeg` 时自动跳过。

联网集成测试默认跳过，需要显式开启（包含 txt→MP3、txt→M4B、EPUB→MP3+M4B）：

```bash
RUN_NETWORK_TESTS=1 uv run python -m unittest tests.test_main -v
```

## 路线图

- [x] 文本文件转 MP3（自动拆分、重试、断点续传、合并）
- [x] 图形界面
- [x] M4B 输出（章节标记、元数据、封面；需要系统 `ffmpeg`）
- [x] 支持 EPUB（spine/TOC 章节、逐章续传、合并 MP3 + 多章节 M4B）
- [ ] 更完整的清洗/兼容：OCR、QuickTime 章节轨道、更多损坏 EPUB 兜底

## 许可证

本项目采用 **GPL-3.0-only**，完整条款见 [LICENSE](LICENSE)。

图形界面依赖 PyQt5；PyQt5 提供 GPLv3 和商业授权两种许可。如果没有获得商业授权，分发本项目或其修改版本时需要遵守 GPLv3，包括向接收者提供对应源代码。
