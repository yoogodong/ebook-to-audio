"""GUI 冒烟测试：offscreen 下能构建窗口和控件。"""

import os
import shutil
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication  # noqa: E402

from ebook_to_audio.gui.app import TextToAudioGUI  # noqa: E402
from tests.epub_test_utils import build_epub, xhtml  # noqa: E402


class GuiSmokeTest(unittest.TestCase):
    def test_window_builds(self):
        app = QApplication.instance() or QApplication([])
        window = TextToAudioGUI()
        try:
            self.assertEqual(window.format_combo.count(), 3)
            self.assertEqual(window.workers_spin.value(), 3)
            self.assertEqual(window.format_combo.itemData(0), "m4b")
            self.assertEqual(window.format_combo.itemData(1), "mp3")
            self.assertEqual(window.format_combo.itemData(2), "both")
            self.assertEqual(
                window.format_combo.itemData(window.format_combo.currentIndex()),
                "m4b",
            )
            self.assertEqual(
                window.voice_combo.itemData(window.voice_combo.currentIndex()),
                "zh-CN-YunjianNeural",
            )
        finally:
            window.close()
            app.processEvents()

    def test_epub_selection_updates_info_and_format(self):
        app = QApplication.instance() or QApplication([])
        temp_dir = tempfile.mkdtemp()
        try:
            epub_path = os.path.join(temp_dir, "book.epub")
            long_text = "这是足够长的正文内容。" * 6
            build_epub(
                epub_path,
                files={
                    "ch1.xhtml": xhtml(
                        f"<h1>第一章</h1><p>{long_text}</p>", "第一章"
                    )
                },
                spine=["ch1.xhtml"],
                toc=[{"title": "第一章", "href": "ch1.xhtml"}],
            )
            window = TextToAudioGUI()
            try:
                window.input_file = epub_path
                window.update_file_info()
                self.assertIn("章节: 1", window.file_info_label.text())
                self.assertEqual(
                    window.format_combo.itemData(window.format_combo.currentIndex()),
                    "m4b",
                )
                self.assertEqual(
                    window.output_edit.text(),
                    os.path.join(temp_dir, "book"),
                )
            finally:
                window.close()
                app.processEvents()
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
