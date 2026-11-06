"""file_handler 的纯函数测试（不联网）。"""

import os
import tempfile
import unittest

from ebook_to_audio.utils.file_handler import (
    check_file_exists,
    get_output_filename,
    read_file,
)


class FileHandlerTest(unittest.TestCase):
    def test_read_file_roundtrip(self):
        with tempfile.NamedTemporaryFile(
            "w", suffix=".txt", delete=False, encoding="utf-8"
        ) as temp:
            temp.write("测试内容。第二句。")
            path = temp.name
        try:
            self.assertEqual(read_file(path), "测试内容。第二句。")
        finally:
            os.unlink(path)

    def test_read_file_missing(self):
        with self.assertRaises(Exception):
            read_file("不存在的文件.txt")

    def test_get_output_filename(self):
        self.assertEqual(get_output_filename("book.txt"), "book.mp3")
        self.assertEqual(get_output_filename("a.b.txt"), "a.b.mp3")
        self.assertEqual(get_output_filename("dir/chapter"), "dir/chapter.mp3")

    def test_check_file_exists(self):
        self.assertFalse(check_file_exists("不存在的文件.txt"))
        fd, path = tempfile.mkstemp()
        os.close(fd)
        try:
            self.assertTrue(check_file_exists(path))
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
