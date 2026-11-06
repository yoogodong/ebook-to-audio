"""EPUB 解析与章节规划测试（离线，标准库生成夹具）。"""

import os
import tempfile
import unittest
import zipfile

from ebook_to_audio.core.epub_processor import (
    EpubError,
    chapter_file_name,
    chapter_slug,
    extract_cover,
    inspect_epub,
    read_epub,
)
from tests.epub_test_utils import build_epub, xhtml

LONG = "这是用于测试的正文内容，长度足够被识别为可朗读文本。" * 4
LONG2 = "第二章的正文内容，同样足够长，用来验证章节切分。" * 4


def toc_link(title, href, children=None):
    return {"title": title, "href": href, "children": children or []}


def toc_group(title, children):
    return {"title": title, "href": "", "children": children}


class EpubProcessorTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.path = os.path.join(self.temp_dir, "book.epub")

    def tearDown(self):
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def build(self, **kwargs):
        files = kwargs.pop(
            "files",
            {
                "ch1.xhtml": xhtml(f"<h1 id='a'>第一章</h1><p>{LONG}</p>", "第一章"),
                "ch2.xhtml": xhtml(f"<h1>第二章</h1><p>{LONG2}</p>", "第二章"),
            },
        )
        spine = kwargs.pop("spine", ["ch1.xhtml", "ch2.xhtml"])
        toc = kwargs.pop(
            "toc",
            [toc_link("第一章 风起", "ch1.xhtml"), toc_link("第二章 云涌", "ch2.xhtml")],
        )
        return build_epub(self.path, files=files, spine=spine, toc=toc, **kwargs)

    def test_epub3_nav_metadata_and_chapters(self):
        self.build()
        meta, chapters = read_epub(self.path)
        self.assertEqual(meta.title, "测试书")
        self.assertEqual(meta.author, "测试作者")
        self.assertEqual(meta.language, "zh")
        self.assertEqual(meta.chapter_count, 2)
        self.assertEqual([c.title for c in chapters], ["第一章 风起", "第二章 云涌"])
        self.assertEqual([c.index for c in chapters], [1, 2])
        self.assertIn("正文内容", chapters[0].text)

    def test_spine_order_wins_over_toc_order(self):
        self.build(spine=["ch2.xhtml", "ch1.xhtml"])
        _meta, chapters = read_epub(self.path)
        self.assertEqual([c.title for c in chapters], ["第二章 云涌", "第一章 风起"])

    def test_nested_toc_uses_deepest_level(self):
        self.build(
            toc=[
                toc_group(
                    "第一卷",
                    [
                        toc_link("第一章", "ch1.xhtml"),
                        toc_link("第二章", "ch2.xhtml"),
                    ],
                )
            ]
        )
        _meta, chapters = read_epub(self.path)
        self.assertEqual([c.title for c in chapters], ["第一章", "第二章"])

    def test_anchor_split_within_one_document(self):
        body = (
            "<h1 id='part-a'>上半章</h1>"
            f"<p>{LONG}</p>"
            "<h1 id='part-b'>下半章</h1>"
            f"<p>{LONG2}</p>"
        )
        self.build(
            files={"ch1.xhtml": xhtml(body, "整章")},
            spine=["ch1.xhtml"],
            toc=[
                toc_link("上半章", "ch1.xhtml#part-a"),
                toc_link("下半章", "ch1.xhtml#part-b"),
            ],
        )
        _meta, chapters = read_epub(self.path)
        self.assertEqual(len(chapters), 2)
        self.assertEqual([c.title for c in chapters], ["上半章", "下半章"])
        self.assertIn("上半章", chapters[0].text)
        self.assertIn("下半章", chapters[1].text)
        self.assertNotIn("下半章", chapters[0].text)

    def test_heading_fallback_without_toc(self):
        body = (
            f"<h1>第一部分</h1><p>{LONG}</p>"
            f"<h1>第二部分</h1><p>{LONG2}</p>"
        )
        self.build(
            files={"ch1.xhtml": xhtml(body, "整章")},
            spine=["ch1.xhtml"],
            toc=[],
        )
        _meta, chapters = read_epub(self.path)
        self.assertEqual([c.title for c in chapters], ["第一部分", "第二部分"])

    def test_filters_linear_no_and_non_content(self):
        files = {
            "cover.xhtml": xhtml(f"<p>{LONG}</p>", "封面"),
            "copyright.xhtml": (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<html xmlns="http://www.w3.org/1999/xhtml" '
                'xmlns:epub="http://www.idpf.org/2007/ops"><head><title>版权</title></head>'
                f'<body epub:type="copyright-page"><p>{LONG}</p></body></html>'
            ),
            "ch1.xhtml": xhtml(f"<h1>第一章</h1><p>{LONG}</p>", "第一章"),
        }
        self.build(
            files=files,
            spine=[("cover.xhtml", "no"), "copyright.xhtml", "ch1.xhtml"],
            toc=[toc_link("第一章", "ch1.xhtml")],
            guide=[("cover", "cover.xhtml"), ("copyright-page", "copyright.xhtml")],
        )
        _meta, chapters = read_epub(self.path)
        self.assertEqual(len(chapters), 1)
        self.assertEqual(chapters[0].title, "第一章")

    def test_ncx_fallback_for_epub2(self):
        self.build(version=2)
        _meta, chapters = read_epub(self.path)
        self.assertEqual([c.title for c in chapters], ["第一章 风起", "第二章 云涌"])

    def test_single_chapter_without_toc_or_headings(self):
        self.build(
            files={
                "ch1.xhtml": xhtml(f"<p>{LONG}</p>", "正文标题"),
            },
            spine=["ch1.xhtml"],
            toc=[],
            version=2,
        )
        _meta, chapters = read_epub(self.path)
        self.assertEqual(len(chapters), 1)
        self.assertEqual(chapters[0].title, "正文标题")

    def test_gb18030_document_is_decoded(self):
        markup = (
            '<?xml version="1.0" encoding="gb18030"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>编码</title></head>'
            f"<body><h1>第一章</h1><p>{LONG}</p></body></html>"
        ).encode("gb18030")
        self.build(
            files={"ch1.xhtml": markup},
            spine=["ch1.xhtml"],
            toc=[toc_link("第一章", "ch1.xhtml")],
        )
        _meta, chapters = read_epub(self.path)
        self.assertEqual(len(chapters), 1)
        self.assertIn("正文内容", chapters[0].text)

    def test_drm_encrypted_spine_is_rejected(self):
        self.build()
        encryption = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container" '
            'xmlns:enc="http://www.w3.org/2001/04/xmlenc#">'
            "<enc:EncryptedData><enc:CipherData>"
            '<enc:CipherReference URI="OEBPS/ch1.xhtml"/>'
            "</enc:CipherData></enc:EncryptedData></encryption>"
        )
        with zipfile.ZipFile(self.path, "a") as archive:
            archive.writestr("META-INF/encryption.xml", encryption)
        with self.assertRaises(EpubError):
            read_epub(self.path)

    def test_inspect_and_cover_extraction(self):
        cover_name = "cover.jpg"
        self.build(cover=(cover_name, b"\xff\xd8\xff\xe0fakejpeg"))
        meta = inspect_epub(self.path)
        self.assertEqual(meta.chapter_count, 2)
        self.assertEqual(meta.cover_href, f"OEBPS/{cover_name}")

        cover_path = extract_cover(self.path, meta.cover_href, self.temp_dir)
        self.assertIsNotNone(cover_path)
        self.assertTrue(os.path.exists(cover_path))
        with open(cover_path, "rb") as file:
            self.assertEqual(file.read(), b"\xff\xd8\xff\xe0fakejpeg")

    def test_guide_toc_on_combined_file_keeps_body(self):
        long_body = LONG * 20
        long_body2 = LONG2 * 20
        body = (
            "<p><a href='#a'>目录链接一</a></p>"
            "<p><a href='#b'>目录链接二</a></p>"
            f"<h1 id='a'>第一章</h1><p>{long_body}</p>"
            f"<h1 id='b'>第二章</h1><p>{long_body2}</p>"
        )
        self.build(
            files={"book.xhtml": xhtml(body, "整本")},
            spine=["book.xhtml"],
            toc=[
                {"title": "目录", "href": "book.xhtml"},
                {"title": "第一章", "href": "book.xhtml#a"},
                {"title": "第二章", "href": "book.xhtml#b"},
            ],
            guide=[("toc", "book.xhtml")],
            version=2,
        )
        _meta, chapters = read_epub(self.path)
        self.assertEqual([c.title for c in chapters], ["第一章", "第二章"])
        for chapter in chapters:
            self.assertNotIn("目录链接", chapter.text)

    def test_link_heavy_guide_toc_document_is_skipped(self):
        links = "".join(
            f"<p><a href='ch1.xhtml'>章节条目{i} 这是目录链接文本</a></p>"
            for i in range(120)
        )
        self.build(
            files={
                "toc.xhtml": xhtml(links, "目录"),
                "ch1.xhtml": xhtml(f"<h1>第一章</h1><p>{LONG}</p>", "第一章"),
            },
            spine=["toc.xhtml", "ch1.xhtml"],
            toc=[{"title": "第一章", "href": "ch1.xhtml"}],
            guide=[("toc", "toc.xhtml")],
        )
        _meta, chapters = read_epub(self.path)
        self.assertEqual(len(chapters), 1)
        self.assertEqual(chapters[0].title, "第一章")

    def test_slug_and_file_name(self):
        self.assertEqual(chapter_slug("第一章：风/起？"), "第一章_风_起")
        self.assertEqual(chapter_slug("   "), "chapter")
        self.assertEqual(
            chapter_file_name(3, "第一章：风/起", part=2, parts=3),
            "003_第一章_风_起_part02.mp3",
        )
        self.assertEqual(chapter_file_name(1, "序"), "001_序.mp3")


if __name__ == "__main__":
    unittest.main()
