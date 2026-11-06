"""端到端集成测试（需要联网，默认跳过）。

运行方式：
    RUN_NETWORK_TESTS=1 uv run python -m unittest tests.test_main -v
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest

from ebook_to_audio.core.audio_export import ffmpeg_available
from ebook_to_audio.core.converter import convert_text_to_formats
from ebook_to_audio.core.epub_converter import convert_epub_file
from ebook_to_audio.core.tts_processor import convert_text_file
try:
    from tests.epub_test_utils import build_epub, xhtml
except ModuleNotFoundError:  # 直接运行 tests/test_main.py 时
    from epub_test_utils import build_epub, xhtml

RUN_NETWORK = os.environ.get("RUN_NETWORK_TESTS") == "1"


@unittest.skipUnless(RUN_NETWORK, "设置 RUN_NETWORK_TESTS=1 后才运行联网测试")
class EndToEndTest(unittest.TestCase):
    def test_short_text_conversion(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_file = os.path.join(temp_dir, "test.txt")
            with open(input_file, "w", encoding="utf-8") as file:
                file.write("这是一个测试文本，用于测试文本转音频功能。")

            output_file = convert_text_file(input_file)

            self.assertTrue(os.path.exists(output_file))
            self.assertGreater(os.path.getsize(output_file), 0)
            self.assertFalse(os.path.exists(output_file + ".parts"))

    @unittest.skipUnless(ffmpeg_available(), "需要本地 ffmpeg/ffprobe")
    def test_short_text_to_m4b(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_file = os.path.join(temp_dir, "test.txt")
            with open(input_file, "w", encoding="utf-8") as file:
                file.write("这是一个用于测试 m4b 导出的短文本。")

            result = convert_text_to_formats(
                input_file,
                output_format="m4b",
                title="测试书名",
                author="测试作者",
            )

            self.assertIsNotNone(result.mp3)
            self.assertIsNotNone(result.m4b)
            self.assertTrue(os.path.exists(result.m4b))
            self.assertGreater(os.path.getsize(result.m4b), 0)
            self.assertFalse(os.path.exists(result.m4b + ".part.m4b"))

            ffprobe = shutil.which("ffprobe")
            assert ffprobe is not None
            completed = subprocess.run(
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-show_format",
                    "-show_chapters",
                    "-show_streams",
                    "-of",
                    "json",
                    result.m4b,
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            data = json.loads(completed.stdout)
            self.assertIn("mp4", data["format"]["format_name"])
            self.assertEqual(data["format"]["tags"]["title"], "测试书名")
            self.assertEqual(data["format"]["tags"]["artist"], "测试作者")
            self.assertEqual(len(data["chapters"]), 1)
            self.assertEqual(data["chapters"][0]["tags"]["title"], "测试书名")
            audio_streams = [
                stream
                for stream in data["streams"]
                if stream["codec_type"] == "audio"
            ]
            self.assertEqual(audio_streams[0]["codec_name"], "aac")

    @unittest.skipUnless(ffmpeg_available(), "需要本地 ffmpeg/ffprobe")
    def test_short_epub_to_mp3_and_m4b(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = os.path.join(temp_dir, "book.epub")
            text = "这是用于 EPUB 端到端测试的正文内容。" * 3
            build_epub(
                epub_path,
                files={
                    "ch1.xhtml": xhtml(f"<h1>第一章</h1><p>{text}</p>", "第一章"),
                    "ch2.xhtml": xhtml(f"<h1>第二章</h1><p>{text}</p>", "第二章"),
                },
                spine=["ch1.xhtml", "ch2.xhtml"],
                toc=[
                    {"title": "第一章 风起", "href": "ch1.xhtml"},
                    {"title": "第二章 云涌", "href": "ch2.xhtml"},
                ],
                title="测试书名",
                author="测试作者",
            )

            base = os.path.join(temp_dir, "epub-output")
            result = convert_epub_file(
                epub_path,
                base,
                output_format="both",
                max_concurrency=2,
            )

            self.assertEqual(result.chapter_count, 2)
            self.assertIsNotNone(result.mp3)
            self.assertIsNotNone(result.m4b)
            self.assertTrue(os.path.exists(result.mp3))
            self.assertTrue(os.path.exists(result.m4b))
            chapter_files = [
                name
                for name in os.listdir(result.chapters_dir)
                if name.endswith(".mp3")
            ]
            self.assertEqual(len(chapter_files), 2)

            ffprobe = shutil.which("ffprobe")
            assert ffprobe is not None
            completed = subprocess.run(
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-show_format",
                    "-show_chapters",
                    "-of",
                    "json",
                    result.m4b,
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            data = json.loads(completed.stdout)
            self.assertEqual(data["format"]["tags"]["title"], "测试书名")
            self.assertEqual(
                [chapter["tags"]["title"] for chapter in data["chapters"]],
                ["第一章 风起", "第二章 云涌"],
            )


if __name__ == "__main__":
    unittest.main()
