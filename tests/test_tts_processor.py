"""转换流程测试：用假的合成函数代替网络请求。"""

import asyncio
import os
import tempfile
import unittest
from unittest.mock import patch

from ebook_to_audio.core.tts_processor import (
    ConversionCancelled,
    convert_text_to_audio,
)

# 三句各 ~501 字，按 900 字上限会被切成 3 块
SENTENCE = "甲" * 500 + "。"
TEXT = SENTENCE * 3


class FakeSynth:
    """可编程的假 synthesize_chunk：记录调用顺序，按需返回或抛错。"""

    def __init__(self, fail_on_call: int | None = None):
        self.calls: list[str] = []
        self.fail_on_call = fail_on_call

    async def __call__(self, text, voice, **kwargs):
        self.calls.append(text)
        if self.fail_on_call is not None and len(self.calls) == self.fail_on_call:
            raise RuntimeError("模拟网络错误")
        return f"<{len(self.calls)}>".encode("utf-8")


class ConvertTextToAudioTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.output = os.path.join(self.temp_dir, "out.mp3")
        self.cache_dir = self.output + ".parts"

    def tearDown(self):
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def run_convert(self, fake, **kwargs):
        kwargs.setdefault("max_concurrency", 1)
        return asyncio.run(
            convert_text_to_audio(TEXT, self.output, "zh-CN-YunxiNeural", **kwargs)
        )

    def test_merges_chunks_in_order_and_cleans_cache(self):
        fake = FakeSynth()
        with patch("ebook_to_audio.core.tts_processor.synthesize_chunk", fake):
            result = self.run_convert(fake)

        self.assertEqual(result, self.output)
        self.assertEqual(len(fake.calls), 3)
        with open(self.output, "rb") as file:
            data = file.read()
        self.assertEqual(data, b"<1><2><3>")
        self.assertFalse(os.path.exists(self.cache_dir))
        self.assertFalse(os.path.exists(self.output + ".part"))

    def test_progress_reaches_100(self):
        fake = FakeSynth()
        reported: list[int] = []
        with patch("ebook_to_audio.core.tts_processor.synthesize_chunk", fake):
            self.run_convert(fake, progress=reported.append)

        self.assertEqual(reported[-1], 100)
        self.assertEqual(reported, sorted(reported))

    def test_failure_keeps_cache_for_resume(self):
        fake = FakeSynth(fail_on_call=2)
        with patch("ebook_to_audio.core.tts_processor.synthesize_chunk", fake):
            with self.assertRaises(Exception):
                self.run_convert(fake)

        self.assertFalse(os.path.exists(self.output))
        self.assertFalse(os.path.exists(self.output + ".part"))
        cached = os.listdir(self.cache_dir)
        self.assertEqual(len(cached), 1, "已完成的块应保留以便续传")

    def test_resume_only_synthesizes_missing_chunks(self):
        first = FakeSynth(fail_on_call=2)
        with patch("ebook_to_audio.core.tts_processor.synthesize_chunk", first):
            with self.assertRaises(Exception):
                self.run_convert(first)

        second = FakeSynth()
        with patch("ebook_to_audio.core.tts_processor.synthesize_chunk", second):
            self.run_convert(second)

        self.assertEqual(len(second.calls), 2, "第一块应复用缓存")
        self.assertTrue(os.path.exists(self.output))
        self.assertFalse(os.path.exists(self.cache_dir))

    def test_cancel_before_start(self):
        fake = FakeSynth()
        with patch("ebook_to_audio.core.tts_processor.synthesize_chunk", fake):
            with self.assertRaises(ConversionCancelled):
                self.run_convert(fake, cancel_check=lambda: True)

        self.assertEqual(fake.calls, [])
        self.assertFalse(os.path.exists(self.output))

    def test_empty_text_rejected(self):
        with self.assertRaises(Exception):
            asyncio.run(convert_text_to_audio("   \n  ", self.output))

    def test_changed_text_invalidates_stale_cache(self):
        stale = os.path.join(self.cache_dir, "00001-deadbeef0000.mp3")
        os.makedirs(self.cache_dir)
        with open(stale, "wb") as file:
            file.write(b"stale")

        fake = FakeSynth()
        with patch("ebook_to_audio.core.tts_processor.synthesize_chunk", fake):
            self.run_convert(fake)

        self.assertEqual(len(fake.calls), 3)


class ConcurrentSynth:
    """带 await 的假合成器，用于观察并发度。"""

    def __init__(self):
        self.active = 0
        self.max_active = 0
        self.calls = 0

    async def __call__(self, text, voice, **kwargs):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.calls += 1
        try:
            await asyncio.sleep(0.02)
        finally:
            self.active -= 1
        return b"x"


class ConcurrencyTest(unittest.TestCase):
    def test_respects_max_concurrency(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = os.path.join(temp_dir, "out.mp3")
            fake = ConcurrentSynth()
            text = SENTENCE * 6  # 6 块

            with patch(
                "ebook_to_audio.core.tts_processor.synthesize_chunk", fake
            ):
                asyncio.run(
                    convert_text_to_audio(
                        text, output, "zh-CN-YunxiNeural", max_concurrency=2
                    )
                )

            self.assertEqual(fake.calls, 6)
            self.assertGreaterEqual(fake.max_active, 2)
            self.assertLessEqual(fake.max_active, 2)
            with open(output, "rb") as file:
                self.assertEqual(file.read(), b"x" * 6)


if __name__ == "__main__":
    unittest.main()
