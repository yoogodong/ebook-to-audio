"""命令行参数与错误处理测试（不联网）。"""

import contextlib
import io
import os
import tempfile
import unittest
from unittest.mock import patch

from ebook_to_audio.core.converter import ConversionResult
from ebook_to_audio.main import build_parser, main


class CliTest(unittest.TestCase):
    def test_parser_defaults(self):
        args = build_parser().parse_args(["book.txt"])
        self.assertEqual(args.input_file, "book.txt")
        self.assertIsNone(args.output)
        self.assertEqual(args.voice, "zh-CN-YunjianNeural")
        self.assertEqual(args.rate, "+0%")
        self.assertEqual(args.output_format, "m4b")
        self.assertEqual(args.m4b_bitrate, "64k")
        self.assertFalse(args.no_mp3)
        self.assertEqual(args.workers, 3)
        self.assertEqual(args.chapter_level, "auto")
        self.assertIsNone(args.max_chapter_chars)
        self.assertFalse(args.no_chapters)
        self.assertFalse(args.read_title)

    def test_help_renders(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            with self.assertRaises(SystemExit) as context:
                build_parser().parse_args(["--help"])
        self.assertEqual(context.exception.code, 0)
        self.assertIn("--rate", buffer.getvalue())

    def test_parser_options(self):
        args = build_parser().parse_args(
            [
                "book.txt",
                "-o",
                "out.mp3",
                "-v",
                "zh-CN-XiaoxiaoNeural",
                "--rate=-10%",
                "--format",
                "m4b",
                "--m4b-bitrate",
                "96k",
                "--title",
                "书名",
                "--author",
                "作者",
                "--cover",
                "cover.jpg",
                "--no-mp3",
                "--workers",
                "5",
            ]
        )
        self.assertEqual(args.output, "out.mp3")
        self.assertEqual(args.voice, "zh-CN-XiaoxiaoNeural")
        self.assertEqual(args.rate, "-10%")
        self.assertEqual(args.output_format, "m4b")
        self.assertEqual(args.m4b_bitrate, "96k")
        self.assertEqual(args.title, "书名")
        self.assertEqual(args.author, "作者")
        self.assertEqual(args.cover, "cover.jpg")
        self.assertTrue(args.no_mp3)
        self.assertEqual(args.workers, 5)

    def test_epub_options(self):
        args = build_parser().parse_args(
            [
                "book.epub",
                "--chapter-level",
                "2",
                "--max-chapter-chars",
                "5000",
                "--no-chapters",
                "--read-title",
            ]
        )
        self.assertEqual(args.chapter_level, "2")
        self.assertEqual(args.max_chapter_chars, 5000)
        self.assertTrue(args.no_chapters)
        self.assertTrue(args.read_title)

    def test_epub_dispatch_uses_both_format(self):
        with tempfile.NamedTemporaryFile(suffix=".epub", delete=False) as temp:
            path = temp.name
        try:
            result = ConversionResult(
                mp3="out.mp3",
                m4b="out.m4b",
                chapter_count=2,
                chapters_dir="out_chapters",
            )
            buffer = io.StringIO()
            with (
                patch(
                    "ebook_to_audio.main.convert_epub_file", return_value=result
                ) as epub_mock,
                patch("ebook_to_audio.main.convert_text_to_formats") as text_mock,
                contextlib.redirect_stdout(buffer),
            ):
                code = main([path])
            self.assertEqual(code, 0)
            epub_mock.assert_called_once()
            text_mock.assert_not_called()
            self.assertEqual(epub_mock.call_args.kwargs["output_format"], "m4b")
            self.assertIn("章节数: 2", buffer.getvalue())
        finally:
            os.unlink(path)

    def test_missing_file_returns_error(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main(["不存在的文件.txt"])
        self.assertEqual(code, 1)
        self.assertIn("不存在", buffer.getvalue())

    def test_no_input_and_eof_returns_usage_error(self):
        buffer = io.StringIO()
        with patch("builtins.input", side_effect=EOFError):
            with contextlib.redirect_stdout(buffer):
                code = main([])
        self.assertEqual(code, 2)
        self.assertIn("用法", buffer.getvalue())

    def test_empty_text_returns_error(self):
        with tempfile.NamedTemporaryFile(
            "w", suffix=".txt", delete=False, encoding="utf-8"
        ) as temp:
            temp.write("   \n")
            path = temp.name
        try:
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
                code = main([path])
            self.assertEqual(code, 1)
            self.assertIn("为空", buffer.getvalue())
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
