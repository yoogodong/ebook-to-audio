"""split_text 的纯函数测试（不联网）。"""

import unittest

from ebook_to_audio.core.tts_processor import _find_break_point, split_text


class SplitTextTest(unittest.TestCase):
    def assert_blocks_valid(self, text: str, max_size: int) -> list[str]:
        chunks = split_text(text, max_size)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), max_size)
            self.assertTrue(chunk, "不应产生空块")
        self.assertEqual("".join(chunks), text, "切块后拼接应还原原文")
        return chunks

    def test_empty_text(self):
        self.assertEqual(split_text(""), [])

    def test_short_text_single_chunk(self):
        text = "这是一个测试文本，用于测试文本转音频功能。"
        self.assertEqual(split_text(text, 900), [text])

    def test_packs_multiple_sentences_into_one_chunk(self):
        text = "第一句。第二句。第三句。"
        chunks = split_text(text, 100)
        self.assertEqual(chunks, [text])

    def test_splits_on_sentence_boundaries(self):
        text = "甲" * 40 + "。" + "乙" * 40 + "。" + "丙" * 40 + "。"
        chunks = split_text(text, 50)
        self.assertGreater(len(chunks), 1)
        self.assertEqual("".join(chunks), text)
        self.assertTrue(chunks[0].endswith("。"))

    def test_single_long_sentence_is_force_split(self):
        text = "字" * 3000
        chunks = self.assert_blocks_valid(text, 900)
        self.assertGreaterEqual(len(chunks), 4)

    def test_long_sentence_after_short_chunk_stays_within_limit(self):
        # 回归测试：修复前这里会产生 2001 字的超限块
        text = "你好。" + "很长" * 1000 + "。"
        chunks = self.assert_blocks_valid(text, 900)
        self.assertLessEqual(max(len(chunk) for chunk in chunks), 900)

    def test_english_sentences(self):
        text = "First sentence. Second sentence! Third one? Fourth: done."
        chunks = self.assert_blocks_valid(text, 30)
        self.assertTrue(any(chunk.endswith(".") for chunk in chunks))

    def test_newline_is_a_break_point(self):
        text = "标题\n" + "正文" * 100
        chunks = self.assert_blocks_valid(text, 100)
        self.assertTrue(chunks[0].endswith("\n"))

    def test_break_point_prefers_punctuation(self):
        sentence = "a" * 50 + "。" + "b" * 50
        # 在第 51 个字符（"。" 之后）切分比硬切好
        self.assertEqual(_find_break_point(sentence, 60), 51)

    def test_break_point_falls_back_to_hard_cut(self):
        sentence = "a" * 200
        self.assertEqual(_find_break_point(sentence, 100), 100)

    def test_invalid_chunk_size(self):
        with self.assertRaises(ValueError):
            split_text("文本", 0)


if __name__ == "__main__":
    unittest.main()
