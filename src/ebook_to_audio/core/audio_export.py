"""把已生成的 mp3 导出为带章节标记和元数据的 m4b。

只依赖标准库和系统 ffmpeg/ffprobe，不负责 TTS。
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from typing import Callable

ProgressCallback = Callable[[int], None]
CancelCheck = Callable[[], bool]

INSTALL_HINT = (
    "需要系统 ffmpeg/ffprobe：macOS 可执行 `brew install ffmpeg`，"
    "Debian/Ubuntu 可执行 `sudo apt install ffmpeg`。"
)


class ExportError(RuntimeError):
    """m4b 导出失败。"""


class ExportCancelled(ExportError):
    """m4b 导出被取消。"""


@dataclass(frozen=True)
class ChapterAudio:
    index: int
    title: str
    path: str
    duration_ms: int | None = None


@dataclass(frozen=True)
class AudiobookMeta:
    title: str
    author: str | None = None
    album: str | None = None
    cover_path: str | None = None


def ffmpeg_available() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def require_ffmpeg() -> tuple[str, str]:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise ExportError(INSTALL_HINT)
    return ffmpeg, ffprobe


def probe_duration_ms(path: str) -> int:
    """用 ffprobe 读取音频时长（毫秒）。"""
    _, ffprobe = require_ffmpeg()
    command = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        path,
    ]
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode != 0:
        raise ExportError(
            f"读取音频时长失败：{os.path.basename(path)}：{completed.stderr.strip()}"
        )
    try:
        seconds = float(json.loads(completed.stdout)["format"]["duration"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ExportError(f"无法解析音频时长：{path}") from exc
    return max(1, round(seconds * 1000))


def build_concat_list(paths: list[str]) -> str:
    """生成 ffmpeg concat demuxer 使用的文件列表。"""
    lines = []
    for path in paths:
        escaped = os.path.abspath(path).replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    return "\n".join(lines) + "\n"


def _sanitize_metadata_value(value: str) -> str:
    # 元数据值不能跨行；反斜杠是 FFMETADATA1 的转义符
    return " ".join(str(value).split()).replace("\\", "\\\\")


def build_ffmetadata(
    meta: AudiobookMeta, chapters: list[tuple[str, int, int]]
) -> str:
    """生成 FFMETADATA1：全局标签 + [CHAPTER] 章节块。

    chapters 的每项为 (title, start_ms, end_ms)。
    """
    lines = [";FFMETADATA1"]
    tags = [
        ("title", meta.title),
        ("artist", meta.author),
        ("album", meta.album or meta.title),
        ("genre", "Audiobook"),
    ]
    for key, value in tags:
        if value:
            lines.append(f"{key}={_sanitize_metadata_value(value)}")

    for title, start, end in chapters:
        if end <= start:
            continue
        lines.extend(
            [
                "",
                "[CHAPTER]",
                "TIMEBASE=1/1000",
                f"START={start}",
                f"END={end}",
                f"title={_sanitize_metadata_value(title)}",
            ]
        )
    return "\n".join(lines) + "\n"


def build_timeline(
    durations_ms: list[int], actual_total_ms: int | None = None
) -> list[tuple[int, int]]:
    """把每章时长换算成 (start_ms, end_ms)。

    传入 actual_total_ms 时按比例缩放，使最后一章正好结束于实际总时长，
    抵消 AAC 编码的 priming/padding 带来的偏差。
    """
    durations = [max(1, int(value)) for value in durations_ms]
    if not durations:
        return []

    if actual_total_ms and actual_total_ms > 0:
        source_total = sum(durations)
        durations = [
            max(1, round(value * actual_total_ms / source_total))
            for value in durations
        ]
        # 修正四舍五入误差，保证累计时长精确等于实际总时长
        durations[-1] = max(1, durations[-1] + actual_total_ms - sum(durations))

    timeline: list[tuple[int, int]] = []
    cursor = 0
    for duration in durations:
        timeline.append((cursor, cursor + duration))
        cursor += duration
    return timeline


def export_m4b(
    chapters: list[ChapterAudio],
    output_file: str,
    meta: AudiobookMeta,
    *,
    bitrate: str = "64k",
    progress: ProgressCallback | None = None,
    cancel_check: CancelCheck | None = None,
) -> str:
    """把若干章节 mp3 合成为单个 m4b，返回输出路径。

    分两遍执行：第一遍编码 AAC 并写入封面；ffprobe 读实际时长后，
    第二遍以 copy 方式 remux，写入精确的章节标记和元数据。
    """
    ffmpeg, _ = require_ffmpeg()
    if not chapters:
        raise ExportError("没有可导出的章节")

    prepared: list[tuple[ChapterAudio, int]] = []
    for chapter in chapters:
        if not os.path.exists(chapter.path):
            raise ExportError(f"章节音频不存在：{chapter.path}")
        duration = chapter.duration_ms or probe_duration_ms(chapter.path)
        prepared.append((chapter, max(1, int(duration))))

    total_ms = sum(duration for _, duration in prepared)
    output_file = os.path.abspath(output_file)
    os.makedirs(os.path.dirname(output_file) or ".", exist_ok=True)
    partial = output_file + ".part.m4b"

    try:
        with tempfile.TemporaryDirectory(prefix="ebook2audio-m4b-") as temp_dir:
            concat_path = os.path.join(temp_dir, "chapters.ffconcat")
            with open(concat_path, "w", encoding="utf-8") as file:
                file.write(build_concat_list([chapter.path for chapter, _ in prepared]))

            encoded_path = os.path.join(temp_dir, "encoded.m4b")
            _encode(
                ffmpeg,
                concat_path,
                encoded_path,
                meta,
                bitrate,
                total_ms,
                progress,
                cancel_check,
            )

            actual_ms = probe_duration_ms(encoded_path)
            timeline = build_timeline(
                [duration for _, duration in prepared], actual_ms
            )
            metadata = build_ffmetadata(
                meta,
                [
                    (chapter.title, start, end)
                    for (chapter, _), (start, end) in zip(prepared, timeline)
                ],
            )
            metadata_path = os.path.join(temp_dir, "metadata.txt")
            with open(metadata_path, "w", encoding="utf-8") as file:
                file.write(metadata)

            if progress:
                progress(95)
            _remux(ffmpeg, encoded_path, metadata_path, partial, cancel_check)

        os.replace(partial, output_file)
    except BaseException:
        _remove_file(partial)
        raise

    if progress:
        progress(100)
    return output_file


def _encode(
    ffmpeg: str,
    concat_path: str,
    encoded_path: str,
    meta: AudiobookMeta,
    bitrate: str,
    total_ms: int,
    progress: ProgressCallback | None,
    cancel_check: CancelCheck | None,
) -> None:
    command = [
        ffmpeg,
        "-y",
        "-nostdin",
        "-nostats",
        "-loglevel",
        "error",
        "-progress",
        "pipe:1",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        concat_path,
    ]
    if meta.cover_path:
        command.extend(["-i", meta.cover_path])

    command.extend(["-map", "0:a"])
    if meta.cover_path:
        command.extend(["-map", "1:v"])

    command.extend(
        [
            "-map_metadata",
            "-1",
            "-c:a",
            "aac",
            "-b:a",
            bitrate,
            "-ac",
            "1",
            "-ar",
            "44100",
        ]
    )
    if meta.cover_path:
        command.extend(["-c:v", "mjpeg", "-disposition:v", "attached_pic"])

    for key, value in (
        ("title", meta.title),
        ("artist", meta.author),
        ("album", meta.album or meta.title),
        ("genre", "Audiobook"),
    ):
        if value:
            command.extend(["-metadata", f"{key}={value}"])

    command.extend(["-f", "ipod", encoded_path])
    _run_ffmpeg(command, total_ms, progress, cancel_check, 0, 90)


def _remux(
    ffmpeg: str,
    encoded_path: str,
    metadata_path: str,
    partial: str,
    cancel_check: CancelCheck | None,
) -> None:
    command = [
        ffmpeg,
        "-y",
        "-nostdin",
        "-loglevel",
        "error",
        "-i",
        encoded_path,
        "-i",
        metadata_path,
        "-map",
        "0",
        "-map_metadata",
        "1",
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        "-f",
        "ipod",
        partial,
    ]
    _run_ffmpeg(command, 0, None, cancel_check, 95, 100)


def _run_ffmpeg(
    command: list[str],
    total_ms: int,
    progress: ProgressCallback | None,
    cancel_check: CancelCheck | None,
    progress_start: int,
    progress_end: int,
) -> None:
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    lines: queue.Queue[str | None] = queue.Queue()

    def pump() -> None:
        for line in process.stdout:
            lines.put(line)
        lines.put(None)

    pump_thread = threading.Thread(target=pump, daemon=True)
    pump_thread.start()

    last_percent = progress_start
    try:
        while True:
            if cancel_check and cancel_check():
                _terminate(process)
                raise ExportCancelled("m4b 导出已取消")

            try:
                line = lines.get(timeout=0.2)
            except queue.Empty:
                if process.poll() is not None:
                    break
                continue
            if line is None:
                break

            line = line.strip()
            if not line.startswith(("out_time_us=", "out_time_ms=")):
                continue
            try:
                microseconds = int(line.split("=", 1)[1] or 0)
            except ValueError:
                continue
            if total_ms <= 0 or not progress:
                continue
            ratio = min(1.0, microseconds / 1000 / total_ms)
            percent = progress_start + round(ratio * (progress_end - progress_start))
            if percent > last_percent:
                progress(percent)
                last_percent = percent

        return_code = process.wait()
        error_output = process.stderr.read() if process.stderr else ""
        if return_code != 0:
            detail = error_output.strip().splitlines()[-5:]
            raise ExportError(
                "ffmpeg 导出失败（退出码 "
                f"{return_code}）：{' '.join(detail)[-500:]}"
            )
    finally:
        if process.poll() is None:
            _terminate(process)
        pump_thread.join(timeout=1.0)
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass


def _terminate(process: subprocess.Popen) -> None:
    process.terminate()
    try:
        process.wait(3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _remove_file(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass
