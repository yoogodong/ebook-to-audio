# ebook-to-audio

**English** | [简体中文](README.zh-CN.md)

Convert e-book text into speech (MP3 / M4B) with Microsoft Edge TTS through the [`edge-tts`](https://github.com/rany2/edge-tts) library. It is free and does not require an API key.

## Features

- **M4B is the default output format; the default Chinese narrator is Yun Jian (`zh-CN-YunjianNeural`)**
- `.txt` to `.m4b`, `.mp3`, or both, with automatic sentence-aware splitting into chunks of at most 900 characters
- `.epub` to a chapter-marked M4B plus per-chapter MP3 files, with an optional merged MP3
- EPUB parsing uses only the Python standard library: spine reading order, EPUB3 nav / EPUB2 NCX tables of contents, TOC-anchor splitting, and heading (`h1`-`h6`) fallback
- Chunked synthesis with automatic retries and configurable concurrency
- Resumable conversions: intermediate chunks are cached in `<output-file>.parts/`; EPUB jobs also use a `manifest.json` to skip completed chapters
- Merge all chapters into one MP3; caches are removed after success and retained after failure or cancellation
- M4B output with AAC audio, chapter markers, title/author/genre metadata, and an optional cover image (requires system `ffmpeg` / `ffprobe`)
- Command-line interface and a PyQt5 GUI with drag and drop, voice selection, preview, progress, and cancellation
- Adjustable voice, rate, volume, and pitch

## Requirements

This project uses [uv](https://docs.astral.sh/uv/) to manage its environment:

```bash
uv sync
```

M4B output also requires system `ffmpeg` and `ffprobe`:

```bash
brew install ffmpeg          # macOS
sudo apt install ffmpeg      # Debian/Ubuntu
```

## Usage

### GUI

```bash
uv run ebook-to-audio-gui
# or
scripts/run_gui.sh
```

Drag in a `.txt` or `.epub` file, choose the voice, output format, and output path, then click **Start conversion**. During conversion, click **Cancel** to stop; completed chunks and chapters remain available for resuming. M4B output requires `ffmpeg` on the local machine.

![ebook-to-audio GUI](docs/images/gui.png)

### Command line

```bash
# Convert a.txt; the default output is a.m4b (default voice: Yun Jian)
uv run ebook-to-audio a.txt

# Select a voice and rate, and set the output path
uv run ebook-to-audio a.txt -v zh-CN-XiaoxiaoNeural --rate=-10% -o out.m4b

# Produce MP3 only
uv run ebook-to-audio a.txt -f mp3

# Produce both MP3 and M4B
uv run ebook-to-audio a.txt -f both -o book

# Increase concurrent requests (default: 3; range: 1-8; higher values may be rate-limited)
uv run ebook-to-audio a.txt -j 4

# Prompt for the input file when no file is supplied
uv run ebook-to-audio
```

`-o` specifies the output basename; the actual files are generated as `<basename>.mp3` and/or `<basename>.m4b`, depending on the selected format. In M4B mode, intermediate MP3 files are kept by default. Add `--no-mp3` to delete them after a successful export.

### EPUB

```bash
# Default output: book.m4b + book_chapters/ (default voice: Yun Jian)
uv run ebook-to-audio book.epub

# Produce both MP3 and M4B
uv run ebook-to-audio book.epub -f both -o out/book

# Produce MP3 only
uv run ebook-to-audio book.epub -f mp3

# Split chapters using the second TOC level
uv run ebook-to-audio book.epub --chapter-level 2

# Split oversized chapters at 20,000 characters and read the title at the start of each chapter
uv run ebook-to-audio book.epub --max-chapter-chars 20000 --read-title

# Remove chapter MP3 files after export and keep only the complete book
uv run ebook-to-audio book.epub --no-chapters
```

EPUB output:

- `<basename>_chapters/001_<chapter-title>.mp3`: one audio file per chapter, numbered in spine/TOC order
- `<basename>_chapters/manifest.json`: source-file hash, voice settings, chapter-text hashes, and status; unchanged chapters are skipped on subsequent runs
- `<basename>.mp3`: the merged book (generated with `-f mp3` or `-f both`)
- `<basename>.m4b`: the default output, with chapter markers, title/author/genre metadata, and a cover image

M4B is the default format. If `ffmpeg` is unavailable, the command reports an error; use `-f mp3` to produce MP3 only.

Chapter ownership follows the spine (reading order); the TOC supplies only titles and hierarchy. If a TOC is missing, the converter falls back to `h1`-`h6` headings or treats the whole file as one chapter. Very short chapters with almost no text are skipped. DRM-protected, fixed-layout, or image-only chapters are skipped or reported as errors; OCR is not supported.

The tool can also be invoked with `uv run python -m ebook_to_audio.main a.txt` or `scripts/run.sh a.txt`.


## Project layout

```text
scripts/                 # Launch scripts
src/ebook_to_audio/
  main.py                    # CLI entry point (txt / epub dispatch)
  core/tts_processor.py      # Text splitting, Edge TTS synthesis, resumable cache, concurrency
  core/converter.py          # TXT output orchestration
  core/audio_export.py       # M4B export: AAC, chapter markers, metadata, cover
  core/epub_processor.py     # EPUB parsing, chapter planning, content cleanup
  core/epub_converter.py     # Per-chapter EPUB TTS, merging, M4B, manifest
  gui/app.py                 # PyQt5 interface
  gui/icon.py                # Runtime-generated window icon
  utils/file_handler.py      # File I/O and output-name derivation
tests/                       # unittest suite (offline by default)
```

## Tests

```bash
uv run python -m unittest discover -s tests -v
```

M4B tests generate silent audio locally and use `ffmpeg` to verify chapters and metadata. They are skipped automatically when `ffmpeg` is unavailable.

Network integration tests are skipped by default. Enable them explicitly to cover txt-to-MP3, txt-to-M4B, and EPUB-to-MP3+M4B:

```bash
RUN_NETWORK_TESTS=1 uv run python -m unittest tests.test_main -v
```

## Roadmap

- [x] Text files to MP3 (automatic splitting, retries, resumable cache, merging)
- [x] GUI
- [x] M4B output (chapter markers, metadata, cover; requires system `ffmpeg`)
- [x] EPUB support (spine/TOC chapters, per-chapter resumption, merged MP3 + multi-chapter M4B)
- [ ] More complete cleanup and compatibility: OCR, QuickTime chapter tracks, and more fallbacks for malformed EPUB files

## License

This project is licensed under **GPL-3.0-only**. See [LICENSE](LICENSE) for the full terms.

The GUI depends on PyQt5, which is available under GPLv3 or a commercial license. If you distribute this project or a modified version without a commercial PyQt license, you must comply with GPLv3, including providing the corresponding source code.
