"""EPUB 转换编排：逐章 TTS、合并 mp3、导出 m4b、manifest 续传。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from typing import Callable

from ebook_to_audio.core import tts_processor
from ebook_to_audio.core.audio_export import (
    AudiobookMeta,
    ChapterAudio,
    ExportCancelled,
    ExportError,
    export_m4b,
    require_ffmpeg,
)
from ebook_to_audio.core.converter import OUTPUT_FORMATS, ConversionResult
from ebook_to_audio.core.epub_processor import (
    Chapter,
    EpubError,
    chapter_file_name,
    extract_cover,
    read_epub,
)
from ebook_to_audio.utils.file_handler import get_output_filename

ProgressCallback = Callable[[int], None]
CancelCheck = Callable[[], bool]

MANIFEST_VERSION = 1


@dataclass(frozen=True)
class _WorkItem:
    order: int
    chapter: Chapter
    part: int
    parts: int
    title: str
    text: str
    text_sha1: str
    file_name: str
    audio_path: str


def _output_base(input_file: str, output_file: str | None) -> str:
    base = output_file or get_output_filename(input_file)
    lower = base.lower()
    for extension in (".mp3", ".m4b", ".epub"):
        if lower.endswith(extension):
            return base[: -len(extension)]
    return base


def _file_sha1(path: str) -> str:
    digest = hashlib.sha1()
    with open(path, "rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def split_chapter_text(text: str, max_chars: int | None) -> list[str]:
    """按 max_chars 在段落/句子边界切分超长章节。"""
    if not max_chars or max_chars <= 0 or len(text) <= max_chars:
        return [text]

    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    parts: list[str] = []
    current = ""

    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            if current:
                parts.append(current)
                current = ""
            for piece in tts_processor.split_text(paragraph, max_chars):
                stripped = piece.strip()
                if stripped:
                    parts.append(stripped)
            continue
        if not current:
            current = paragraph
        elif len(current) + 2 + len(paragraph) <= max_chars:
            current += "\n\n" + paragraph
        else:
            parts.append(current)
            current = paragraph

    if current:
        parts.append(current)
    return parts or [text]


def _load_manifest(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_manifest(path: str, data: dict) -> None:
    temp_path = path + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
    os.replace(temp_path, path)


def _merge_mp3(audio_files: list[str], output_file: str) -> None:
    """按顺序拼接章节 mp3，成功后原子替换。"""
    if not audio_files:
        raise tts_processor.ConversionError("没有可合并的章节音频")
    partial = output_file + ".part"
    try:
        with open(partial, "wb") as output:
            for path in audio_files:
                with open(path, "rb") as part:
                    shutil.copyfileobj(part, output)
        os.replace(partial, output_file)
    except BaseException:
        try:
            os.unlink(partial)
        except OSError:
            pass
        raise


def _build_work_items(
    chapters: list[Chapter],
    chapters_dir: str,
    *,
    read_title: bool,
    max_chapter_chars: int | None,
) -> list[_WorkItem]:
    items: list[_WorkItem] = []
    order = 0
    for chapter in chapters:
        pieces = split_chapter_text(chapter.text, max_chapter_chars)
        parts = len(pieces)
        for part_index, piece in enumerate(pieces, start=1):
            order += 1
            title = (
                chapter.title
                if parts == 1
                else f"{chapter.title}（{part_index}/{parts}）"
            )
            text = f"{chapter.title}。\n\n{piece}" if read_title else piece
            text_sha1 = hashlib.sha1(text.encode("utf-8")).hexdigest()
            file_name = chapter_file_name(
                chapter.index,
                chapter.title,
                part_index if parts > 1 else None,
                parts,
            )
            items.append(
                _WorkItem(
                    order=order,
                    chapter=chapter,
                    part=part_index,
                    parts=parts,
                    title=title,
                    text=text,
                    text_sha1=text_sha1,
                    file_name=file_name,
                    audio_path=os.path.join(chapters_dir, file_name),
                )
            )
    return items


def _prune_stale_chapters(chapters_dir: str, expected: set[str]) -> None:
    for name in os.listdir(chapters_dir):
        if not re.fullmatch(r"\d{3}_.+\.mp3", name) or name in expected:
            continue
        try:
            os.unlink(os.path.join(chapters_dir, name))
        except OSError:
            pass


def convert_epub_file(
    input_file: str,
    output_file: str | None = None,
    voice: str = tts_processor.DEFAULT_VOICE,
    *,
    output_format: str = "m4b",
    chapter_level: str | int = "auto",
    max_chapter_chars: int | None = None,
    read_title: bool = False,
    keep_chapters: bool = True,
    title: str | None = None,
    author: str | None = None,
    cover_path: str | None = None,
    m4b_bitrate: str = "64k",
    rate: str = "+0%",
    volume: str = "+0%",
    pitch: str = "+0Hz",
    progress: ProgressCallback | None = None,
    cancel_check: CancelCheck | None = None,
    max_concurrency: int = tts_processor.DEFAULT_CONCURRENCY,
    retries: int = tts_processor.DEFAULT_RETRIES,
) -> ConversionResult:
    """把 EPUB 逐章转成音频，返回实际生成的 mp3/m4b。"""
    if output_format not in OUTPUT_FORMATS:
        raise ValueError(f"不支持输出格式: {output_format}")
    if not input_file.lower().endswith(".epub"):
        raise EpubError(f"不是 EPUB 文件：{input_file}")

    wants_mp3 = output_format in ("mp3", "both")
    wants_m4b = output_format in ("m4b", "both")
    if wants_m4b:
        try:
            require_ffmpeg()
        except ExportError as exc:
            raise tts_processor.ConversionError(str(exc)) from exc
    if cover_path and not os.path.exists(cover_path):
        raise tts_processor.ConversionError(f"封面文件不存在：{cover_path}")

    meta, chapters = read_epub(input_file, chapter_level=chapter_level)
    base = _output_base(input_file, output_file)
    chapters_dir = base + "_chapters"
    os.makedirs(chapters_dir, exist_ok=True)
    manifest_path = os.path.join(chapters_dir, "manifest.json")

    items = _build_work_items(
        chapters,
        chapters_dir,
        read_title=read_title,
        max_chapter_chars=max_chapter_chars,
    )
    _prune_stale_chapters(chapters_dir, {item.file_name for item in items})

    settings = {
        "voice": voice,
        "rate": rate,
        "volume": volume,
        "pitch": pitch,
        "chapter_level": str(chapter_level),
        "max_chapter_chars": max_chapter_chars,
        "read_title": read_title,
    }
    source = {
        "path": os.path.abspath(input_file),
        "size": os.path.getsize(input_file),
        "sha1": _file_sha1(input_file),
    }
    previous = _load_manifest(manifest_path)
    resume_settings_ok = previous.get("settings") == settings
    previous_entries = {
        entry.get("file_name"): entry
        for entry in previous.get("chapters", [])
        if isinstance(entry, dict)
    }

    manifest = {
        "version": MANIFEST_VERSION,
        "source": source,
        "settings": settings,
        "book": {
            "title": title or meta.title,
            "author": author or meta.author,
            "language": meta.language,
        },
        "chapters": [
            {
                "order": item.order,
                "chapter_index": item.chapter.index,
                "part": item.part,
                "parts": item.parts,
                "title": item.title,
                "href": item.chapter.href,
                "fragment": item.chapter.fragment,
                "text_sha1": item.text_sha1,
                "file_name": item.file_name,
                "status": "pending",
            }
            for item in items
        ],
    }

    completed_chars = 0
    total_chars = sum(len(item.text) for item in items) or 1
    tts_share = 85 if wants_m4b else 95

    def report_tts_progress(item: _WorkItem, percent: int) -> None:
        if not progress:
            return
        done = completed_chars + len(item.text) * percent / 100
        progress(min(tts_share, round(done / total_chars * tts_share)))

    def report_completed() -> None:
        if progress:
            progress(min(tts_share, round(completed_chars / total_chars * tts_share)))

    try:
        _save_manifest(manifest_path, manifest)

        for index, item in enumerate(items):
            entry = manifest["chapters"][index]
            if cancel_check and cancel_check():
                raise tts_processor.ConversionCancelled("转换已取消")

            previous_entry = previous_entries.get(item.file_name)
            can_resume = (
                resume_settings_ok
                and previous_entry is not None
                and previous_entry.get("text_sha1") == item.text_sha1
                and os.path.exists(item.audio_path)
                and os.path.getsize(item.audio_path) > 0
            )
            if can_resume:
                entry["status"] = "done"
                completed_chars += len(item.text)
                report_completed()
                _save_manifest(manifest_path, manifest)
                continue

            asyncio.run(
                tts_processor.convert_text_to_audio(
                    item.text,
                    item.audio_path,
                    voice,
                    rate=rate,
                    volume=volume,
                    pitch=pitch,
                    progress=lambda percent, current=item: report_tts_progress(
                        current, percent
                    ),
                    cancel_check=cancel_check,
                    retries=retries,
                    max_concurrency=max_concurrency,
                )
            )
            entry["status"] = "done"
            completed_chars += len(item.text)
            report_completed()
            _save_manifest(manifest_path, manifest)
    except BaseException:
        _save_manifest(manifest_path, manifest)
        raise

    audio_files = [item.audio_path for item in items]
    missing = [path for path in audio_files if not os.path.exists(path)]
    if missing:
        raise tts_processor.ConversionError(f"章节音频缺失：{missing[0]}")

    merged_mp3: str | None = None
    if wants_mp3:
        merged_mp3 = base + ".mp3"
        if progress:
            progress(tts_share if wants_m4b else 95)
        _merge_mp3(audio_files, merged_mp3)

    m4b_file: str | None = None
    m4b_start = 90 if wants_mp3 and wants_m4b else 85
    if wants_m4b:
        m4b_file = base + ".m4b"
        book_title = title or meta.title
        book_author = author or meta.author
        with tempfile.TemporaryDirectory(prefix="ebook2audio-epub-") as temp_dir:
            resolved_cover = cover_path or extract_cover(
                input_file, meta.cover_href, temp_dir
            )
            try:
                export_m4b(
                    [
                        ChapterAudio(
                            index=item.order,
                            title=item.title,
                            path=item.audio_path,
                        )
                        for item in items
                    ],
                    m4b_file,
                    AudiobookMeta(
                        title=book_title,
                        author=book_author,
                        album=book_title,
                        cover_path=resolved_cover,
                    ),
                    bitrate=m4b_bitrate,
                    progress=lambda percent: progress(
                        m4b_start + round(percent * (100 - m4b_start) / 100)
                    )
                    if progress
                    else None,
                    cancel_check=cancel_check,
                )
            except ExportCancelled as exc:
                raise tts_processor.ConversionCancelled(str(exc)) from exc
            except ExportError as exc:
                raise tts_processor.ConversionError(str(exc)) from exc

    if not keep_chapters and m4b_file:
        for item in items:
            try:
                os.unlink(item.audio_path)
            except OSError:
                pass
            shutil.rmtree(item.audio_path + ".parts", ignore_errors=True)
        for entry in manifest["chapters"]:
            entry["status"] = "deleted"
        _save_manifest(manifest_path, manifest)

    if progress:
        progress(100)

    return ConversionResult(
        mp3=merged_mp3 if merged_mp3 and os.path.exists(merged_mp3) else None,
        m4b=m4b_file if m4b_file and os.path.exists(m4b_file) else None,
        chapter_count=len(chapters),
        chapters_dir=chapters_dir,
    )


__all__ = ["convert_epub_file", "split_chapter_text"]
