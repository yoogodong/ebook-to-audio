"""输出格式编排测试（不联网，m4b 导出用 mock）。"""

import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from ebook_to_audio.core import converter, tts_processor
from ebook_to_audio.core.audio_export import ExportError
from ebook_to_audio.core.converter import convert_text_to_formats, output_paths


class OutputPathsTest(unittest.TestCase):
    def test_default_mp3(self):
        self.assertEqual(
            output_paths("book.txt", None, "mp3"), ("book.mp3", "book.m4b")
        )

    def test_strips_known_extension(self):
        self.assertEqual(
            output_paths("book.txt", "dir/out.m4b", "m4b"),
            ("dir/out.mp3", "dir/out.m4b"),
        )
        self.assertEqual(
            output_paths("book.txt", "dir/out.mp3", "both"),
            ("dir/out.mp3", "dir/out.m4b"),
        )

    def test_keeps_unknown_extension(self):
        self.assertEqual(
            output_paths("book.txt", "dir/out", "mp3"),
            ("dir/out.mp3", "dir/out.m4b"),
        )

    def test_invalid_format(self):
        with self.assertRaises(ValueError):
            output_paths("book.txt", None, "flac")


class ConvertFormatsTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.input_file = os.path.join(self.temp_dir, "book.txt")
        with open(self.input_file, "w", encoding="utf-8") as file:
            file.write("测试内容。")

        self.tts_calls: list[str] = []
        self.export_calls: list[tuple] = []
        self.progress: list[int] = []

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def fake_tts(self, input_file, output_file, voice, **kwargs):
        self.tts_calls.append(output_file)
        with open(output_file, "wb") as file:
            file.write(b"mp3")
        callback = kwargs.get("progress")
        if callback:
            callback(100)
        return output_file

    def fake_export(self, chapters, output_file, meta, **kwargs):
        self.export_calls.append((chapters, meta))
        with open(output_file, "wb") as file:
            file.write(b"m4b")
        callback = kwargs.get("progress")
        if callback:
            callback(100)
        return output_file

    def convert(self, **kwargs):
        with (
            patch.object(
                converter.tts_processor, "convert_text_file", side_effect=self.fake_tts
            ),
            patch.object(
                converter, "require_ffmpeg", return_value=("ffmpeg", "ffprobe")
            ),
            patch.object(converter, "_export_m4b", side_effect=self.fake_export),
        ):
            return convert_text_to_formats(
                self.input_file,
                None,
                "zh-CN-YunxiNeural",
                progress=self.progress.append,
                **kwargs,
            )

    def test_mp3_only(self):
        result = self.convert(output_format="mp3")
        self.assertEqual(result.mp3, os.path.join(self.temp_dir, "book.mp3"))
        self.assertIsNone(result.m4b)
        self.assertEqual(len(self.export_calls), 0)
        self.assertEqual(self.progress[-1], 100)

    def test_m4b_keeps_mp3_by_default(self):
        result = self.convert(output_format="m4b")
        mp3_path = os.path.join(self.temp_dir, "book.mp3")
        m4b_path = os.path.join(self.temp_dir, "book.m4b")
        self.assertEqual(result.mp3, mp3_path)
        self.assertEqual(result.m4b, m4b_path)
        self.assertTrue(os.path.exists(mp3_path))
        self.assertTrue(os.path.exists(m4b_path))

        chapters, meta = self.export_calls[0]
        self.assertEqual(len(chapters), 1)
        self.assertEqual(chapters[0].path, mp3_path)
        self.assertEqual(chapters[0].title, "book")
        self.assertEqual(meta.title, "book")
        self.assertEqual(meta.author, None)

        self.assertIn(85, self.progress)
        self.assertEqual(self.progress[-1], 100)

    def test_m4b_no_mp3_removes_intermediate(self):
        result = self.convert(output_format="m4b", keep_mp3=False)
        self.assertIsNone(result.mp3)
        self.assertIsNotNone(result.m4b)
        self.assertFalse(os.path.exists(os.path.join(self.temp_dir, "book.mp3")))
        self.assertTrue(os.path.exists(os.path.join(self.temp_dir, "book.m4b")))

    def test_both_keeps_mp3_even_with_no_mp3(self):
        result = self.convert(output_format="both", keep_mp3=False)
        self.assertIsNotNone(result.mp3)
        self.assertIsNotNone(result.m4b)
        self.assertTrue(os.path.exists(os.path.join(self.temp_dir, "book.mp3")))

    def test_mp3_does_not_need_ffmpeg(self):
        with (
            patch.object(
                converter.tts_processor, "convert_text_file", side_effect=self.fake_tts
            ),
            patch.object(
                converter,
                "require_ffmpeg",
                side_effect=ExportError("没有 ffmpeg"),
            ),
        ):
            result = convert_text_to_formats(self.input_file, output_format="mp3")
        self.assertIsNotNone(result.mp3)

    def test_m4b_missing_ffmpeg_fails_before_tts(self):
        with (
            patch.object(
                converter.tts_processor, "convert_text_file", side_effect=self.fake_tts
            ),
            patch.object(
                converter,
                "require_ffmpeg",
                side_effect=ExportError("需要系统 ffmpeg"),
            ),
        ):
            with self.assertRaises(tts_processor.ConversionError) as context:
                convert_text_to_formats(self.input_file, output_format="m4b")
        self.assertIn("ffmpeg", str(context.exception))
        self.assertEqual(self.tts_calls, [])

    def test_export_failure_becomes_conversion_error(self):
        with patch.object(
            converter, "_export_m4b", side_effect=ExportError("编码失败")
        ):
            with (
                patch.object(
                    converter.tts_processor,
                    "convert_text_file",
                    side_effect=self.fake_tts,
                ),
                patch.object(
                    converter, "require_ffmpeg", return_value=("ffmpeg", "ffprobe")
                ),
            ):
                with self.assertRaises(tts_processor.ConversionError):
                    convert_text_to_formats(self.input_file, output_format="m4b")

    def test_title_and_author_forwarded(self):
        self.convert(output_format="m4b", title="书名", author="作者")
        _, meta = self.export_calls[0]
        self.assertEqual(meta.title, "书名")
        self.assertEqual(meta.author, "作者")
        self.assertEqual(meta.album, "书名")


if __name__ == "__main__":
    unittest.main()
