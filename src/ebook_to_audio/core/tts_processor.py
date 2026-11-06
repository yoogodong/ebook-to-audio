"""文本转音频核心逻辑：分块、Edge TTS 合成、缓存续传与合并。

CLI 和 GUI 都通过本模块完成转换，避免各自维护一份转换实现。
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
from typing import Callable

import edge_tts

from ebook_to_audio.utils.file_handler import get_output_filename, read_file

# 每次请求的文本块大小。edge-tts 7.x 自身按 4096 字节切分；
# 这里切得更小是为了让进度、重试和续传以块为单位。
MAX_CHUNK_SIZE = 900
DEFAULT_VOICE = "zh-CN-YunjianNeural"
DEFAULT_RETRIES = 3
DEFAULT_CONCURRENCY = 3
MAX_CONCURRENCY = 8
RETRY_BACKOFF_SECONDS = 1.5

# 中英文句末标点；换行也视为可切分位置
SENTENCE_ENDINGS = frozenset("。！？；：.!?;:\n")

ProgressCallback = Callable[[int], None]
CancelCheck = Callable[[], bool]


class ConversionError(RuntimeError):
    """转换失败。"""


class ConversionCancelled(ConversionError):
    """用户取消了转换。"""


def split_text(text: str, max_chunk_size: int = MAX_CHUNK_SIZE) -> list[str]:
    """按句子把文本切成不超过 max_chunk_size 字符的块。"""
    if max_chunk_size <= 0:
        raise ValueError("max_chunk_size 必须大于 0")

    chunks: list[str] = []
    current = ""

    for sentence in _split_into_sentences(text):
        # 单个句子超长时先切开，确保任何块都不会超过上限
        while len(sentence) > max_chunk_size:
            if current:
                chunks.append(current)
                current = ""
            split_at = _find_break_point(sentence, max_chunk_size)
            chunks.append(sentence[:split_at])
            sentence = sentence[split_at:]

        if not sentence:
            continue
        if len(current) + len(sentence) <= max_chunk_size:
            current += sentence
        else:
            if current:
                chunks.append(current)
            current = sentence

    if current:
        chunks.append(current)
    return chunks


def _split_into_sentences(text: str) -> list[str]:
    """按句末标点切句，保留标点。"""
    sentences: list[str] = []
    current = ""
    for char in text:
        current += char
        if char in SENTENCE_ENDINGS:
            sentences.append(current)
            current = ""
    if current.strip():
        sentences.append(current)
    return sentences


def _find_break_point(sentence: str, max_chunk_size: int) -> int:
    """返回不超过 max_chunk_size 的切分位置，尽量落在句末标点之后。"""
    window_start = max(0, max_chunk_size - 100)
    for index in range(max_chunk_size - 1, window_start - 1, -1):
        if sentence[index] in SENTENCE_ENDINGS:
            return index + 1
    return max_chunk_size


async def synthesize_chunk(
    text: str,
    voice: str = DEFAULT_VOICE,
    *,
    rate: str = "+0%",
    volume: str = "+0%",
    pitch: str = "+0Hz",
    retries: int = DEFAULT_RETRIES,
    cancel_check: CancelCheck | None = None,
) -> bytes:
    """把一段文本合成为 mp3 字节，失败时按次数重试。"""
    last_error: Exception | None = None

    for attempt in range(1, retries + 1):
        _raise_if_cancelled(cancel_check)
        try:
            communicate = edge_tts.Communicate(
                text, voice, rate=rate, volume=volume, pitch=pitch
            )
            parts: list[bytes] = []
            async for chunk in communicate.stream():
                _raise_if_cancelled(cancel_check)
                if chunk["type"] == "audio":
                    parts.append(chunk["data"])
            if parts:
                return b"".join(parts)
            last_error = ConversionError("Edge TTS 没有返回音频数据")
        except ConversionCancelled:
            raise
        except Exception as exc:  # 网络、服务端错误等
            last_error = exc
        if attempt < retries:
            await asyncio.sleep(RETRY_BACKOFF_SECONDS * attempt)

    raise ConversionError(f"音频合成失败（已重试 {retries} 次）：{last_error}") from last_error


async def convert_text_to_audio(
    text: str,
    output_file: str,
    voice: str = DEFAULT_VOICE,
    *,
    rate: str = "+0%",
    volume: str = "+0%",
    pitch: str = "+0Hz",
    progress: ProgressCallback | None = None,
    cancel_check: CancelCheck | None = None,
    retries: int = DEFAULT_RETRIES,
    max_concurrency: int = DEFAULT_CONCURRENCY,
) -> str:
    """把文本合成为单个 mp3，逐块缓存并并发请求 Edge TTS。

    每块音频保存在 ``<output_file>.parts/`` 下，重跑相同文本和音色时直接复用；
    全部成功后缓存目录会被删除，失败或取消时保留以便续传。
    max_concurrency 控制同时进行的请求数（1–8）。
    """
    if not text or not text.strip():
        raise ConversionError("文本内容为空，没有可转换的内容")

    chunks = split_text(text, MAX_CHUNK_SIZE)
    if not chunks:
        raise ConversionError("文本内容为空，没有可转换的内容")

    workers = max(1, min(int(max_concurrency), MAX_CONCURRENCY))
    cache_dir = _cache_dir_for(output_file)
    os.makedirs(cache_dir, exist_ok=True)
    keys = [_chunk_key(chunk, voice, rate, volume, pitch) for chunk in chunks]
    _prune_cache(cache_dir, keys)

    total = len(chunks)
    completed = 0
    last_percent = -1

    def report_progress() -> None:
        nonlocal completed, last_percent
        completed += 1
        if not progress:
            return
        percent = round(completed * 100 / total)
        if percent != last_percent:
            last_percent = percent
            progress(percent)

    semaphore = asyncio.Semaphore(workers)

    async def process_chunk(index: int, chunk: str, key: str) -> None:
        chunk_path = os.path.join(cache_dir, _cache_name(index, key))
        if _has_audio(chunk_path):
            report_progress()
            return

        async with semaphore:
            _raise_if_cancelled(cancel_check)
            data = await synthesize_chunk(
                chunk,
                voice,
                rate=rate,
                volume=volume,
                pitch=pitch,
                retries=retries,
                cancel_check=cancel_check,
            )
            # 先写临时文件再原子替换，避免并发或中断留下半截缓存
            temp_path = chunk_path + ".tmp"
            with open(temp_path, "wb") as file:
                file.write(data)
            os.replace(temp_path, chunk_path)
        report_progress()

    _raise_if_cancelled(cancel_check)
    try:
        if workers == 1:
            for index, (chunk, key) in enumerate(zip(chunks, keys), start=1):
                await process_chunk(index, chunk, key)
        else:
            tasks = [
                asyncio.create_task(process_chunk(index, chunk, key))
                for index, (chunk, key) in enumerate(zip(chunks, keys), start=1)
            ]
            try:
                await asyncio.gather(*tasks)
            except BaseException:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise
        _merge_cache_files(cache_dir, keys, output_file)
    except BaseException:
        # 保留缓存以便续传，清掉未完成的临时输出
        _remove_file(output_file + ".part")
        raise

    shutil.rmtree(cache_dir, ignore_errors=True)
    return output_file


def convert_text_file(
    input_file: str,
    output_file: str | None = None,
    voice: str = DEFAULT_VOICE,
    *,
    rate: str = "+0%",
    volume: str = "+0%",
    pitch: str = "+0Hz",
    progress: ProgressCallback | None = None,
    cancel_check: CancelCheck | None = None,
    retries: int = DEFAULT_RETRIES,
    max_concurrency: int = DEFAULT_CONCURRENCY,
) -> str:
    """同步入口：读取文本文件并转换为 mp3，返回输出路径。"""
    text = read_file(input_file)
    if not text or not text.strip():
        raise ConversionError("文本内容为空，请选择包含内容的文本文件")

    target = output_file or get_output_filename(input_file)
    asyncio.run(
        convert_text_to_audio(
            text,
            target,
            voice,
            rate=rate,
            volume=volume,
            pitch=pitch,
            progress=progress,
            cancel_check=cancel_check,
            retries=retries,
            max_concurrency=max_concurrency,
        )
    )
    return target


def _raise_if_cancelled(cancel_check: CancelCheck | None) -> None:
    if cancel_check is not None and cancel_check():
        raise ConversionCancelled("转换已取消")


def _cache_dir_for(output_file: str) -> str:
    return output_file + ".parts"


def _cache_name(index: int, key: str) -> str:
    return f"{index:05d}-{key}.mp3"


def _chunk_key(chunk: str, voice: str, rate: str, volume: str, pitch: str) -> str:
    payload = f"{voice}|{rate}|{volume}|{pitch}|{chunk}".encode("utf-8")
    return hashlib.sha1(payload).hexdigest()[:12]


def _has_audio(path: str) -> bool:
    return os.path.exists(path) and os.path.getsize(path) > 0


def _prune_cache(cache_dir: str, keys: list[str]) -> None:
    """删除与当前文本/音色不匹配的旧缓存块。"""
    expected = {_cache_name(index, key) for index, key in enumerate(keys, start=1)}
    for name in os.listdir(cache_dir):
        if name not in expected:
            _remove_file(os.path.join(cache_dir, name))


def _merge_cache_files(cache_dir: str, keys: list[str], output_file: str) -> None:
    """按顺序拼接缓存块，再原子替换为最终输出文件。"""
    temp_output = output_file + ".part"
    with open(temp_output, "wb") as out:
        for index, key in enumerate(keys, start=1):
            with open(os.path.join(cache_dir, _cache_name(index, key)), "rb") as part:
                shutil.copyfileobj(part, out)
    os.replace(temp_output, output_file)


def _remove_file(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass
