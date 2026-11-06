"""命令行入口：把 txt / epub 文件转换为 mp3 / m4b。"""

from __future__ import annotations

import argparse
import threading

from ebook_to_audio.core.converter import OUTPUT_FORMATS, convert_text_to_formats
from ebook_to_audio.core.epub_converter import convert_epub_file
from ebook_to_audio.core.epub_processor import EpubError
from ebook_to_audio.core.tts_processor import (
    DEFAULT_CONCURRENCY,
    DEFAULT_VOICE,
    ConversionCancelled,
    ConversionError,
)
from ebook_to_audio.utils.file_handler import check_file_exists


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ebook-to-audio",
        description="把文本文件或 EPUB 转换为 MP3 / M4B 音频（使用微软 Edge TTS）",
    )
    parser.add_argument(
        "input_file", nargs="?", help="要转换的 .txt 或 .epub 文件；省略时交互输入"
    )
    parser.add_argument(
        "-o",
        "--output",
        help="输出主名或路径（后缀可省略）；默认与输入文件同名",
    )
    parser.add_argument(
        "-f",
        "--format",
        dest="output_format",
        choices=OUTPUT_FORMATS,
        default="m4b",
        help="输出格式：m4b（默认，需要系统 ffmpeg）、mp3 或 both",
    )
    parser.add_argument(
        "-v", "--voice", default=DEFAULT_VOICE, help=f"播音员音色，默认 {DEFAULT_VOICE}"
    )
    parser.add_argument(
        "-j",
        "--workers",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help=f"并发请求数（1–8），默认 {DEFAULT_CONCURRENCY}；过高可能被服务端限流",
    )
    parser.add_argument(
        "--rate",
        default="+0%",
        help="语速；取值以负号开头时用等号连接，例如 --rate=-10%% 或 --rate=+20%%",
    )
    parser.add_argument("--volume", default="+0%", help="音量，例如 --volume=+10%%")
    parser.add_argument("--pitch", default="+0Hz", help="音调，例如 --pitch=+5Hz")
    parser.add_argument(
        "--m4b-bitrate", default="64k", help="m4b 的 AAC 码率，默认 64k"
    )
    parser.add_argument("--cover", help="m4b 封面图片路径（可选）")
    parser.add_argument("--title", help="书名/标题，默认取文件内元数据或文件名")
    parser.add_argument("--author", help="作者，默认取 EPUB 元数据")
    parser.add_argument(
        "--no-mp3",
        action="store_true",
        help="输出格式为 m4b 时，导出成功后删除中间的 mp3 文件（txt 用）",
    )
    parser.add_argument(
        "--chapter-level",
        default="auto",
        help="EPUB 章节层级：auto 或数字（如 1 表示卷/章的第一层）",
    )
    parser.add_argument(
        "--max-chapter-chars",
        type=int,
        default=None,
        help="EPUB 单章超过该字符数时按段落/句子切成多个音频",
    )
    parser.add_argument(
        "--no-chapters",
        action="store_true",
        help="EPUB 成功导出 m4b 后删除章节 mp3",
    )
    parser.add_argument(
        "--read-title",
        action="store_true",
        help="EPUB 每章开始额外朗读章节标题",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.input_file:
        input_file = args.input_file
    else:
        try:
            input_file = input("请输入文本文件名: ").strip()
        except EOFError:
            print("错误: 未提供输入文件。用法: ebook-to-audio <文件>")
            return 2
    if not check_file_exists(input_file):
        print(f"错误: 文件 '{input_file}' 不存在！")
        return 1

    is_epub = input_file.lower().endswith(".epub")
    output_format = args.output_format

    print("欢迎使用文本转音频工具！")
    print(f"输入文件: {input_file}")
    print(f"播音员: {args.voice}")
    print(f"输出格式: {output_format}")
    print(f"并发请求: {args.workers}")

    cancel_event = threading.Event()
    last_percent = -1

    def on_progress(percent: int) -> None:
        nonlocal last_percent
        if percent != last_percent:
            print(f"\r转换进度: {percent}%", end="", flush=True)
            last_percent = percent

    try:
        if is_epub:
            result = convert_epub_file(
                input_file,
                args.output,
                args.voice,
                output_format=output_format,
                chapter_level=args.chapter_level,
                max_chapter_chars=args.max_chapter_chars,
                read_title=args.read_title,
                keep_chapters=not args.no_chapters,
                title=args.title,
                author=args.author,
                cover_path=args.cover,
                m4b_bitrate=args.m4b_bitrate,
                rate=args.rate,
                volume=args.volume,
                pitch=args.pitch,
                progress=on_progress,
                cancel_check=cancel_event.is_set,
                max_concurrency=args.workers,
            )
        else:
            result = convert_text_to_formats(
                input_file,
                args.output,
                args.voice,
                output_format=output_format,
                title=args.title,
                author=args.author,
                cover_path=args.cover,
                m4b_bitrate=args.m4b_bitrate,
                keep_mp3=not args.no_mp3,
                rate=args.rate,
                volume=args.volume,
                pitch=args.pitch,
                progress=on_progress,
                cancel_check=cancel_event.is_set,
                max_concurrency=args.workers,
            )
    except (KeyboardInterrupt, ConversionCancelled):
        cancel_event.set()
        print("\n已取消转换（已完成的章节/块会保留）。")
        return 130
    except (ConversionError, EpubError) as exc:
        print(f"\n错误: {exc}")
        return 1
    except Exception as exc:  # 兜底，避免把 traceback 直接抛给普通用户
        print(f"\n错误: {type(exc).__name__}: {exc}")
        return 1

    print("\n转换完成！")
    if result.chapter_count:
        print(f"章节数: {result.chapter_count}")
    if result.chapters_dir:
        print(f"章节文件: {result.chapters_dir}")
    if result.mp3:
        print(f"MP3: {result.mp3}")
    if result.m4b:
        print(f"M4B: {result.m4b}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
