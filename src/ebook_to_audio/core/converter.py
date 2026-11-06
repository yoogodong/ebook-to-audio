"""按输出格式编排 TTS 与 m4b 导出。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable

from ebook_to_audio.core import tts_processor
from ebook_to_audio.core.audio_export import (
    AudiobookMeta,
    ChapterAudio,
    ExportCancelled,
    ExportError,
    ffmpeg_available,
    require_ffmpeg,
)
from ebook_to_audio.core.audio_export import export_m4b as _export_m4b
from ebook_to_audio.utils.file_handler import get_output_filename

OUTPUT_FORMATS = ("mp3", "m4b", "both")

ProgressCallback = Callable[[int], None]


@dataclass(frozen=True)
class ConversionResult:
    mp3: str | None
    m4b: str | None
    chapter_count: int = 0
    chapters_dir: str | None = None


def _strip_known_extension(path: str) -> str:
    lower = path.lower()
    for extension in (".mp3", ".m4b"):
        if lower.endswith(extension):
            return path[: -len(extension)]
    return path


def output_paths(
    input_file: str, output_file: str | None, output_format: str
) -> tuple[str, str]:
    """返回 (mp3 路径, m4b 路径)。

    用户给的输出路径如果带 .mp3/.m4b 后缀会被去掉，两种格式共用同一主名。
    """
    if output_format not in OUTPUT_FORMATS:
        raise ValueError(f"不支持输出格式: {output_format}")
    base = _strip_known_extension(output_file or get_output_filename(input_file))
    return f"{base}.mp3", f"{base}.m4b"


def convert_text_to_formats(
    input_file: str,
    output_file: str | None = None,
    voice: str = tts_processor.DEFAULT_VOICE,
    *,
    output_format: str = "m4b",
    title: str | None = None,
    author: str | None = None,
    cover_path: str | None = None,
    m4b_bitrate: str = "64k",
    keep_mp3: bool = True,
    rate: str = "+0%",
    volume: str = "+0%",
    pitch: str = "+0Hz",
    progress: ProgressCallback | None = None,
    cancel_check: Callable[[], bool] | None = None,
    retries: int = tts_processor.DEFAULT_RETRIES,
    max_concurrency: int = tts_processor.DEFAULT_CONCURRENCY,
) -> ConversionResult:
    """把 txt 转成 mp3、m4b 或两者，返回实际生成的文件。"""
    if output_format not in OUTPUT_FORMATS:
        raise ValueError(f"不支持输出格式: {output_format}")

    wants_m4b = output_format in ("m4b", "both")
    if wants_m4b:
        try:
            require_ffmpeg()
        except ExportError as exc:
            raise tts_processor.ConversionError(str(exc)) from exc

    mp3_path, m4b_path = output_paths(input_file, output_file, output_format)
    default_title = os.path.splitext(os.path.basename(input_file))[0]
    book_title = title or default_title

    def tts_progress(percent: int) -> None:
        if not progress:
            return
        if wants_m4b:
            progress(round(percent * 0.85))
        else:
            progress(percent)

    tts_processor.convert_text_file(
        input_file,
        mp3_path,
        voice,
        rate=rate,
        volume=volume,
        pitch=pitch,
        progress=tts_progress,
        cancel_check=cancel_check,
        retries=retries,
        max_concurrency=max_concurrency,
    )

    if wants_m4b:
        def export_progress(percent: int) -> None:
            if progress:
                progress(85 + round(percent * 0.15))

        try:
            _export_m4b(
                [ChapterAudio(index=1, title=book_title, path=mp3_path)],
                m4b_path,
                AudiobookMeta(
                    title=book_title,
                    author=author,
                    album=book_title,
                    cover_path=cover_path,
                ),
                bitrate=m4b_bitrate,
                progress=export_progress,
                cancel_check=cancel_check,
            )
        except ExportCancelled as exc:
            raise tts_processor.ConversionCancelled(str(exc)) from exc
        except ExportError as exc:
            raise tts_processor.ConversionError(str(exc)) from exc

        if output_format == "m4b" and not keep_mp3:
            try:
                os.unlink(mp3_path)
            except OSError:
                pass

    return ConversionResult(
        mp3=mp3_path if os.path.exists(mp3_path) else None,
        m4b=m4b_path if wants_m4b and os.path.exists(m4b_path) else None,
    )


__all__ = [
    "OUTPUT_FORMATS",
    "ConversionResult",
    "convert_text_to_formats",
    "ffmpeg_available",
    "output_paths",
]
