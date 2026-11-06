"""EPUB 转换测试：mock TTS，m4b 用本机 ffmpeg 校验（不联网）。"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from ebook_to_audio.core import tts_processor
from ebook_to_audio.core.audio_export import ffmpeg_available
from ebook_to_audio.core.epub_converter import convert_epub_file
from ebook_to_audio.core.epub_processor import chapter_file_name, read_epub
from tests.epub_test_utils import build_epub, xhtml

LONG = "这是用于测试的正文内容，长度足够被识别为可朗读文本。" * 4
LONG2 = "第二章的正文内容，同样足够长，用来验证章节切分。" * 4


class FakeSynth:
    def __init__(self, payload: bytes = b"chunk"):
        self.payload = payload
        self.calls = 0

    async def __call__(self, text, voice, **kwargs):
        self.calls += 1
        return self.payload


def make_silence_mp3(path: str, seconds: float = 0.4) -> None:
    subprocess.run(
        [
            shutil.which("ffmpeg") or "ffmpeg",
            "-y",
            "-nostdin",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=24000:cl=mono",
            "-t",
            str(seconds),
            "-c:a",
            "libmp3lame",
            "-b:a",
            "48k",
            path,
        ],
        check=True,
    )


class EpubConversionTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.epub = os.path.join(self.temp_dir, "book.epub")
        build_epub(
            self.epub,
            files={
                "ch1.xhtml": xhtml(f"<h1>第一章</h1><p>{LONG}</p>", "第一章"),
                "ch2.xhtml": xhtml(f"<h1>第二章</h1><p>{LONG2}</p>", "第二章"),
            },
            spine=["ch1.xhtml", "ch2.xhtml"],
            toc=[
                {"title": "第一章 风起", "href": "ch1.xhtml"},
                {"title": "第二章 云涌", "href": "ch2.xhtml"},
            ],
        )
        self.base = os.path.join(self.temp_dir, "output")
        self.chapters_dir = self.base + "_chapters"

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_mp3_chapters_merge_and_manifest(self):
        fake = FakeSynth()
        with patch(
            "ebook_to_audio.core.tts_processor.synthesize_chunk", fake
        ):
            result = convert_epub_file(
                self.epub, self.base, output_format="mp3"
            )

        self.assertEqual(fake.calls, 2)
        self.assertEqual(result.chapter_count, 2)
        self.assertEqual(result.chapters_dir, self.chapters_dir)
        self.assertEqual(result.mp3, self.base + ".mp3")
        self.assertIsNone(result.m4b)
        self.assertTrue(os.path.exists(result.mp3))

        names = sorted(os.listdir(self.chapters_dir))
        expected = sorted(
            [
                chapter_file_name(1, "第一章 风起"),
                chapter_file_name(2, "第二章 云涌"),
            ]
        )
        self.assertEqual([n for n in names if n.endswith(".mp3")], expected)
        with open(result.mp3, "rb") as file:
            self.assertEqual(file.read(), b"chunkchunk")

        with open(os.path.join(self.chapters_dir, "manifest.json"), encoding="utf-8") as file:
            manifest = json.load(file)
        self.assertEqual(manifest["version"], 1)
        self.assertEqual(manifest["settings"]["voice"], tts_processor.DEFAULT_VOICE)
        self.assertEqual(len(manifest["chapters"]), 2)
        self.assertTrue(all(e["status"] == "done" for e in manifest["chapters"]))

    def test_resume_skips_unchanged_chapters(self):
        first = FakeSynth()
        with patch("ebook_to_audio.core.tts_processor.synthesize_chunk", first):
            convert_epub_file(self.epub, self.base, output_format="mp3")
        self.assertEqual(first.calls, 2)

        second = FakeSynth()
        with patch("ebook_to_audio.core.tts_processor.synthesize_chunk", second):
            result = convert_epub_file(self.epub, self.base, output_format="mp3")
        self.assertEqual(second.calls, 0)
        self.assertTrue(os.path.exists(result.mp3))

    def test_voice_change_regenerates_all_chapters(self):
        first = FakeSynth()
        with patch("ebook_to_audio.core.tts_processor.synthesize_chunk", first):
            convert_epub_file(self.epub, self.base, output_format="mp3")

        second = FakeSynth()
        with patch("ebook_to_audio.core.tts_processor.synthesize_chunk", second):
            convert_epub_file(
                self.epub,
                self.base,
                "zh-CN-XiaoxiaoNeural",
                output_format="mp3",
            )
        self.assertEqual(second.calls, 2)

    def test_cancel_keeps_manifest_and_partial_chapters(self):
        fake = FakeSynth()
        with patch("ebook_to_audio.core.tts_processor.synthesize_chunk", fake):
            with self.assertRaises(tts_processor.ConversionCancelled):
                convert_epub_file(
                    self.epub,
                    self.base,
                    output_format="mp3",
                    cancel_check=lambda: True,
                )
        self.assertEqual(fake.calls, 0)
        self.assertTrue(
            os.path.exists(os.path.join(self.chapters_dir, "manifest.json"))
        )
        self.assertFalse(os.path.exists(self.base + ".mp3"))

    def test_max_chapter_chars_splits_into_parts(self):
        long_text = "段落。" * 400
        build_epub(
            self.epub,
            files={"ch1.xhtml": xhtml(f"<h1>长章</h1><p>{long_text}</p>", "长章")},
            spine=["ch1.xhtml"],
            toc=[{"title": "长章", "href": "ch1.xhtml"}],
        )
        fake = FakeSynth()
        with patch("ebook_to_audio.core.tts_processor.synthesize_chunk", fake):
            result = convert_epub_file(
                self.epub,
                self.base,
                output_format="mp3",
                max_chapter_chars=500,
            )

        self.assertEqual(result.chapter_count, 1)
        files = [n for n in os.listdir(self.chapters_dir) if n.endswith(".mp3")]
        self.assertGreater(len(files), 1)
        self.assertTrue(all("_part" in name for name in files))
        self.assertGreaterEqual(fake.calls, len(files))

    @unittest.skipUnless(ffmpeg_available(), "需要本地 ffmpeg/ffprobe")
    def test_m4b_with_chapter_markers(self):
        silent_mp3 = os.path.join(self.temp_dir, "silence.mp3")
        make_silence_mp3(silent_mp3)
        with open(silent_mp3, "rb") as file:
            payload = file.read()

        fake = FakeSynth(payload)
        with patch("ebook_to_audio.core.tts_processor.synthesize_chunk", fake):
            result = convert_epub_file(
                self.epub, self.base, output_format="both"
            )

        self.assertTrue(os.path.exists(result.mp3))
        self.assertTrue(os.path.exists(result.m4b))

        completed = subprocess.run(
            [
                shutil.which("ffprobe") or "ffprobe",
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
        self.assertIn("mp4", data["format"]["format_name"])
        self.assertEqual(len(data["chapters"]), 2)
        self.assertEqual(
            [chapter["tags"]["title"] for chapter in data["chapters"]],
            ["第一章 风起", "第二章 云涌"],
        )

    def test_read_epub_fixture_has_two_chapters(self):
        _meta, chapters = read_epub(self.epub)
        self.assertEqual(len(chapters), 2)


if __name__ == "__main__":
    unittest.main()
