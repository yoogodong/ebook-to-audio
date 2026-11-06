"""m4b 导出层测试：纯函数 + 本地 ffmpeg 集成（不联网）。"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from ebook_to_audio.core import audio_export
from ebook_to_audio.core.audio_export import (
    AudiobookMeta,
    ChapterAudio,
    ExportCancelled,
    ExportError,
    build_concat_list,
    build_ffmetadata,
    build_timeline,
    export_m4b,
    ffmpeg_available,
    require_ffmpeg,
)


class PureBuilderTest(unittest.TestCase):
    def test_concat_list_escapes_quotes(self):
        text = build_concat_list(["/tmp/a b.mp3", "/tmp/it's.mp3"])
        self.assertIn("file '/tmp/a b.mp3'", text)
        self.assertIn("file '/tmp/it'\\''s.mp3'", text)

    def test_metadata_contains_tags_and_chapters(self):
        meta = AudiobookMeta(title="书名", author="作者")
        text = build_ffmetadata(
            meta, [("第一章 风起", 0, 60000), ("第二章 云涌", 60000, 120000)]
        )
        self.assertTrue(text.startswith(";FFMETADATA1"))
        self.assertIn("title=书名", text)
        self.assertIn("artist=作者", text)
        self.assertIn("album=书名", text)
        self.assertIn("genre=Audiobook", text)
        self.assertEqual(text.count("[CHAPTER]"), 2)
        self.assertIn("TIMEBASE=1/1000", text)
        self.assertIn("START=60000", text)
        self.assertIn("END=120000", text)
        self.assertIn("title=第一章 风起", text)

    def test_metadata_sanitizes_newline_and_backslash(self):
        text = build_ffmetadata(
            AudiobookMeta(title="a\nb\\c"), [("t\nx", 0, 1)]
        )
        self.assertIn("title=a b\\\\c", text)
        self.assertIn("title=t x", text)
        self.assertIn("title=a b\\\\c\n", text)

    def test_metadata_skips_empty_chapter(self):
        text = build_ffmetadata(AudiobookMeta(title="x"), [("bad", 100, 100)])
        self.assertNotIn("[CHAPTER]", text)

    def test_timeline(self):
        self.assertEqual(build_timeline([1000, 2000]), [(0, 1000), (1000, 3000)])
        self.assertEqual(build_timeline([]), [])

    def test_timeline_scaled_to_actual_duration(self):
        timeline = build_timeline([1000, 1000], actual_total_ms=2001)
        self.assertEqual(timeline[0][0], 0)
        self.assertEqual(timeline[-1][1], 2001)
        self.assertEqual(timeline[0][1], timeline[1][0])

    def test_require_ffmpeg_missing(self):
        with patch.object(audio_export.shutil, "which", return_value=None):
            self.assertFalse(ffmpeg_available())
            with self.assertRaises(ExportError):
                require_ffmpeg()


def _ffmpeg() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def _ffprobe() -> str:
    return shutil.which("ffprobe") or "ffprobe"


def make_silence_mp3(path: str, seconds: float) -> None:
    subprocess.run(
        [
            _ffmpeg(),
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


def probe(path: str) -> dict:
    completed = subprocess.run(
        [
            _ffprobe(),
            "-v",
            "error",
            "-show_format",
            "-show_streams",
            "-show_chapters",
            "-of",
            "json",
            path,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(completed.stdout)


@unittest.skipUnless(ffmpeg_available(), "需要本地 ffmpeg/ffprobe")
class ExportM4bIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.chapter1 = os.path.join(self.temp_dir, "1.mp3")
        self.chapter2 = os.path.join(self.temp_dir, "2.mp3")
        make_silence_mp3(self.chapter1, 1)
        make_silence_mp3(self.chapter2, 2)
        self.output = os.path.join(self.temp_dir, "book.m4b")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def chapters(self):
        return [
            ChapterAudio(index=1, title="第一章", path=self.chapter1),
            ChapterAudio(index=2, title="第二章", path=self.chapter2),
        ]

    def test_exports_aac_m4b_with_chapters(self):
        progress: list[int] = []
        export_m4b(
            self.chapters(),
            self.output,
            AudiobookMeta(title="书名", author="作者"),
            progress=progress.append,
        )

        self.assertTrue(os.path.exists(self.output))
        data = probe(self.output)
        self.assertIn("mp4", data["format"]["format_name"])
        self.assertEqual(data["format"]["tags"]["title"], "书名")
        self.assertEqual(data["format"]["tags"]["artist"], "作者")
        self.assertEqual(data["format"]["tags"]["genre"], "Audiobook")

        chapters = data["chapters"]
        self.assertEqual(len(chapters), 2)
        self.assertEqual(chapters[0]["tags"]["title"], "第一章")
        self.assertEqual(chapters[1]["tags"]["title"], "第二章")
        self.assertEqual(chapters[0]["start"], 0)
        self.assertGreater(chapters[1]["start"], 0)
        total_ms = round(float(data["format"]["duration"]) * 1000)
        self.assertAlmostEqual(chapters[-1]["end"], total_ms, delta=100)

        audio_streams = [s for s in data["streams"] if s["codec_type"] == "audio"]
        self.assertEqual(audio_streams[0]["codec_name"], "aac")
        self.assertEqual(progress[-1], 100)
        self.assertEqual(progress, sorted(progress))

    def test_writes_cover_as_attached_picture(self):
        cover = os.path.join(self.temp_dir, "cover.jpg")
        subprocess.run(
            [
                _ffmpeg(),
                "-y",
                "-nostdin",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=gray:s=64x64",
                "-frames:v",
                "1",
                cover,
            ],
            check=True,
        )
        export_m4b(
            self.chapters(),
            self.output,
            AudiobookMeta(title="书名", cover_path=cover),
        )

        data = probe(self.output)
        video = [s for s in data["streams"] if s["codec_type"] == "video"]
        self.assertEqual(len(video), 1)
        self.assertEqual(video[0]["disposition"]["attached_pic"], 1)

    def test_cancel_removes_partial_output(self):
        with self.assertRaises(ExportCancelled):
            export_m4b(
                self.chapters(),
                self.output,
                AudiobookMeta(title="书名"),
                cancel_check=lambda: True,
            )
        self.assertFalse(os.path.exists(self.output))
        self.assertFalse(os.path.exists(self.output + ".part.m4b"))

    def test_missing_chapter_file(self):
        chapter = ChapterAudio(index=1, title="缺失", path="/不存在.mp3")
        with self.assertRaises(ExportError):
            export_m4b([chapter], self.output, AudiobookMeta(title="书名"))


if __name__ == "__main__":
    unittest.main()
