"""EPUB 解析：spine 顺序、TOC 章节、正文清洗。

只使用标准库，不依赖 EbookLib/lxml。设计见 docs/epub-design.md。
"""

from __future__ import annotations

import hashlib
import html
import posixpath
import re
import unicodedata
import zipfile
from collections import Counter
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import unquote
from xml.etree import ElementTree as ET

MAX_MEMBER_BYTES = 20 * 1024 * 1024

# 结构上可判定为非正文的 epub:type / guide 类型
EXCLUDED_TYPES = {
    "cover",
    "titlepage",
    "title-page",
    "copyright-page",
    "toc",
    "index",
    "colophon",
    "nav",
}

BLOCK_TAGS = {
    "p",
    "div",
    "section",
    "article",
    "li",
    "ul",
    "ol",
    "dl",
    "dt",
    "dd",
    "blockquote",
    "figcaption",
    "figure",
    "aside",
    "header",
    "footer",
    "main",
    "pre",
    "table",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
}

SKIP_TAGS = {"script", "style", "head", "noscript", "template", "rt", "rp"}

HEADING_RE = re.compile(r"^h[1-6]$")

INVISIBLE_RE = re.compile(
    r"[​-‏‪-‮⁠-⁤﻿­]"
)


class EpubError(RuntimeError):
    """EPUB 无法解析或没有可朗读内容。"""


@dataclass(frozen=True)
class Chapter:
    index: int
    title: str
    level: int
    href: str
    fragment: str | None
    text: str
    source_key: str


@dataclass(frozen=True)
class EpubBookMeta:
    title: str
    author: str | None = None
    language: str | None = None
    cover_href: str | None = None
    cover_media_type: str | None = None
    chapter_count: int = 0
    warnings: tuple[str, ...] = ()


@dataclass
class _ManifestItem:
    id: str
    href: str
    media_type: str
    properties: str


@dataclass
class _SpineRef:
    idref: str
    linear: bool


@dataclass
class _TocEntry:
    title: str
    href: str
    fragment: str | None
    level: int


@dataclass
class _Document:
    text: str
    anchors: dict[str, int]
    headings: list[tuple[int, str, int]]
    excluded: bool
    title: str | None
    link_chars: int = 0


@dataclass
class _Book:
    opf_path: str
    opf_dir: str
    title: str
    author: str | None
    language: str | None
    manifest: dict[str, _ManifestItem]
    spine: list[_SpineRef]
    spine_paths: list[str]
    guide: dict[str, str]
    toc: list[_TocEntry]
    cover_href: str | None
    cover_media_type: str | None
    warnings: list[str]


def read_epub(
    path: str, *, chapter_level: str | int = "auto"
) -> tuple[EpubBookMeta, list[Chapter]]:
    """读取 EPUB 并规划章节，返回书籍元数据和可朗读章节。"""
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise EpubError(f"无法打开 EPUB（不是有效的 zip）：{exc}") from exc

    with archive:
        book = _parse_book(archive)
        documents: dict[str, _Document] = {}
        for href in book.spine_paths:
            try:
                data = _read_member(archive, href)
            except (KeyError, EpubError) as exc:
                book.warnings.append(f"跳过无法读取的文件 {href}：{exc}")
                continue
            document = _extract_document(data)
            guide_type = book.guide.get(href)
            if guide_type in EXCLUDED_TYPES and _skip_by_guide(document, guide_type):
                continue
            documents[href] = document

        chapters = _plan_chapters(book, documents, chapter_level)
        meta = EpubBookMeta(
            title=book.title,
            author=book.author,
            language=book.language,
            cover_href=book.cover_href,
            cover_media_type=book.cover_media_type,
            chapter_count=len(chapters),
            warnings=tuple(book.warnings),
        )
        if not chapters:
            raise EpubError("没有找到可朗读的正文（可能全是图片、加密内容或目录页）")
        return meta, chapters


def inspect_epub(path: str) -> EpubBookMeta:
    """只读取元数据和章节列表，供 GUI 显示。"""
    meta, _chapters = read_epub(path)
    return meta


def extract_cover(epub_path: str, cover_href: str | None, dest_dir: str) -> str | None:
    """把 EPUB 封面写到 dest_dir，返回文件路径；没有封面返回 None。"""
    if not cover_href:
        return None
    with zipfile.ZipFile(epub_path) as archive:
        real_name = _find_member(archive, cover_href)
        if not real_name:
            return None
        data = _read_member(archive, real_name)

    suffix = posixpath.splitext(cover_href)[1].lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
        suffix = ".jpg"
    target = posixpath.join(dest_dir, "cover" + suffix)
    with open(target, "wb") as file:
        file.write(data)
    return target


def chapter_slug(title: str, max_length: int = 40) -> str:
    """标题转安全文件名片段：只保留中日韩文字、字母、数字。"""
    normalized = unicodedata.normalize("NFC", title or "")
    slug = re.sub(
        r"[^0-9A-Za-z㐀-䶿一-鿿]+", "_", normalized
    ).strip("_")
    slug = re.sub(r"_+", "_", slug)
    return slug[:max_length].strip("_") or "chapter"


def chapter_file_name(
    index: int, title: str, part: int | None = None, parts: int | None = None
) -> str:
    """章节音频文件名，带固定宽度序号。"""
    suffix = f"_part{part:02d}" if parts and parts > 1 and part else ""
    return f"{index:03d}_{chapter_slug(title)}{suffix}.mp3"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _attr_local(element: ET.Element, name: str) -> str | None:
    for key, value in element.attrib.items():
        if key.rsplit("}", 1)[-1].lower() == name.lower() or key.lower() == name.lower():
            return value
    return None


def _epub_types(element: ET.Element) -> set[str]:
    value = _attr_local(element, "type") or ""
    return set(value.split())


def _parse_epub_types(attrs: dict[str, str]) -> set[str]:
    for key, value in attrs.items():
        if key.rsplit("}", 1)[-1].lower() == "type" and ":" in key:
            return set((value or "").split())
    value = attrs.get("epub:type") or ""
    return set(value.split())


def _find_member(archive: zipfile.ZipFile, name: str) -> str | None:
    normalized = posixpath.normpath(name.lstrip("/"))
    names = archive.namelist()
    if normalized in names:
        return normalized
    lowered = {item.lower(): item for item in names}
    return lowered.get(normalized.lower())


def _read_member(archive: zipfile.ZipFile, name: str) -> bytes:
    real_name = _find_member(archive, name)
    if not real_name:
        raise KeyError(name)
    info = archive.getinfo(real_name)
    if info.file_size > MAX_MEMBER_BYTES:
        raise EpubError(f"文件过大，拒绝读取：{real_name}")
    return archive.read(real_name)


def _parse_xml(data: bytes, name: str) -> ET.Element:
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise EpubError(f"XML 解析失败（{name}）：{exc}") from exc


def _resolve(
    base_dir: str, href: str, current_path: str | None = None
) -> tuple[str | None, str | None]:
    """解析相对 href，返回 (zip 内路径, fragment)。"""
    href = html.unescape(unquote(href or ""))
    path_part, _, fragment = href.partition("#")
    fragment = fragment or None
    if not path_part:
        return (current_path, fragment) if current_path else (None, fragment)
    path = posixpath.normpath(posixpath.join(base_dir, path_part))
    if path.startswith("../") or path.startswith("/"):
        raise EpubError(f"非法路径：{href}")
    return path, fragment


def _parse_book(archive: zipfile.ZipFile) -> _Book:
    warnings: list[str] = []
    container_name = _find_member(archive, "META-INF/container.xml")
    opf_path: str | None = None
    if container_name:
        container = _parse_xml(_read_member(archive, container_name), container_name)
        for element in container.iter():
            if _local(element.tag) == "rootfile":
                full_path = element.get("full-path")
                if full_path:
                    opf_path = posixpath.normpath(unquote(full_path))
                    break
    if not opf_path:
        candidates = [
            name for name in archive.namelist() if name.lower().endswith(".opf")
        ]
        if not candidates:
            raise EpubError("不是有效的 EPUB：缺少 container.xml 或 OPF 文件")
        opf_path = candidates[0]
        warnings.append("container.xml 缺失或无效，已回退到第一个 OPF 文件")

    opf_dir = posixpath.dirname(opf_path)
    opf_root = _parse_xml(_read_member(archive, opf_path), opf_path)

    metadata_el = _find_descendant(opf_root, "metadata")
    title = ""
    author = None
    language = None
    cover_id: str | None = None
    metadata_children = list(metadata_el) if metadata_el is not None else []
    for element in metadata_children:
        local = _local(element.tag)
        value = "".join(element.itertext()).strip()
        if local == "title" and not title:
            title = _clean_text_line(value)
        elif local == "creator" and not author:
            author = _clean_text_line(value)
        elif local == "language" and not language:
            language = _clean_text_line(value)
        elif local == "meta":
            prop = element.get("property") or ""
            name = element.get("name") or ""
            content = element.get("content") or ""
            if prop == "rendition:layout" and content == "pre-paginated":
                warnings.append("固定版式 EPUB，正文可能很少或需要 OCR")
            if name.lower() == "cover" and content:
                cover_id = content

    manifest_el = _find_descendant(opf_root, "manifest")
    manifest: dict[str, _ManifestItem] = {}
    if manifest_el is not None:
        for item in manifest_el:
            if _local(item.tag) != "item":
                continue
            item_id = item.get("id")
            href = item.get("href")
            if not item_id or not href:
                continue
            resolved, _fragment = _resolve(opf_dir, href)
            if not resolved:
                continue
            manifest[item_id] = _ManifestItem(
                id=item_id,
                href=resolved,
                media_type=(item.get("media-type") or "").lower(),
                properties=(item.get("properties") or "").lower(),
            )

    spine_el = _find_descendant(opf_root, "spine")
    spine: list[_SpineRef] = []
    ncx_id: str | None = None
    if spine_el is not None:
        ncx_id = spine_el.get("toc")
        for itemref in spine_el:
            if _local(itemref.tag) != "itemref":
                continue
            idref = itemref.get("idref")
            if not idref:
                continue
            linear = (itemref.get("linear") or "yes").lower() != "no"
            spine.append(_SpineRef(idref=idref, linear=linear))

    guide: dict[str, str] = {}
    guide_el = _find_descendant(opf_root, "guide")
    if guide_el is not None:
        for reference in guide_el:
            if _local(reference.tag) != "reference":
                continue
            guide_type = (reference.get("type") or "").lower()
            href = reference.get("href")
            if not guide_type or not href:
                continue
            resolved, _fragment = _resolve(opf_dir, href)
            if resolved:
                guide.setdefault(resolved, guide_type)

    spine_paths: list[str] = []
    for ref in spine:
        item = manifest.get(ref.idref)
        if item is None:
            warnings.append(f"spine 引用了不存在的 manifest id：{ref.idref}")
            continue
        if not ref.linear:
            continue
        if "nav" in item.properties.split():
            continue
        if not _is_xhtml(item):
            continue
        if item.href not in spine_paths:
            spine_paths.append(item.href)

    _check_encryption(archive, spine_paths)

    cover_href: str | None = None
    cover_media_type: str | None = None
    for item in manifest.values():
        if "cover-image" in item.properties.split():
            cover_href = item.href
            cover_media_type = item.media_type
            break
    if cover_href is None and cover_id:
        item = manifest.get(cover_id)
        if item:
            cover_href = item.href
            cover_media_type = item.media_type
    if cover_href is None:
        for href, guide_type in guide.items():
            if guide_type == "cover" and href.lower().endswith(
                (".jpg", ".jpeg", ".png", ".gif", ".webp")
            ):
                cover_href = href
                break

    toc: list[_TocEntry] = []
    nav_item = next(
        (item for item in manifest.values() if "nav" in item.properties.split()),
        None,
    )
    if nav_item:
        try:
            toc = _parse_nav(_read_member(archive, nav_item.href), nav_item.href)
        except (EpubError, KeyError) as exc:
            warnings.append(f"nav 目录解析失败：{exc}")
    if not toc:
        ncx_item = manifest.get(ncx_id) if ncx_id else None
        if ncx_item is None:
            ncx_item = next(
                (
                    item
                    for item in manifest.values()
                    if item.media_type == "application/x-dtbncx+xml"
                ),
                None,
            )
        if ncx_item:
            try:
                toc = _parse_ncx(_read_member(archive, ncx_item.href), ncx_item.href)
            except (EpubError, KeyError) as exc:
                warnings.append(f"NCX 目录解析失败：{exc}")

    if not title:
        title = posixpath.splitext(posixpath.basename(opf_path))[0]

    return _Book(
        opf_path=opf_path,
        opf_dir=opf_dir,
        title=_clean_text_line(title) or "未命名书籍",
        author=author,
        language=language,
        manifest=manifest,
        spine=spine,
        spine_paths=spine_paths,
        guide=guide,
        toc=toc,
        cover_href=cover_href,
        cover_media_type=cover_media_type,
        warnings=warnings,
    )


def _is_xhtml(item: _ManifestItem) -> bool:
    if item.media_type in {"application/xhtml+xml", "text/html"}:
        return True
    return item.href.lower().endswith((".xhtml", ".html", ".htm"))


def _check_encryption(archive: zipfile.ZipFile, spine_paths: list[str]) -> None:
    """检测 DRM：加密了 spine 正文就直接拒绝；字体混淆忽略。"""
    encryption_name = _find_member(archive, "META-INF/encryption.xml")
    if not encryption_name:
        return
    root = _parse_xml(_read_member(archive, encryption_name), encryption_name)
    spine_set = set(spine_paths)
    for element in root.iter():
        if _local(element.tag) != "cipherreference":
            continue
        uri = element.get("URI")
        if not uri:
            continue
        resolved = posixpath.normpath(unquote(uri.partition("#")[0].lstrip("/")))
        if resolved in spine_set:
            raise EpubError("这本 EPUB 受 DRM 保护，无法读取正文")


def _parse_nav(data: bytes, nav_path: str) -> list[_TocEntry]:
    root = _parse_xml(data, nav_path)
    nav_el = None
    for element in root.iter():
        if _local(element.tag) == "nav" and "toc" in _epub_types(element):
            nav_el = element
            break
    if nav_el is None:
        return []

    entries: list[_TocEntry] = []
    base_dir = posixpath.dirname(nav_path)

    def walk(list_el: ET.Element, level: int) -> None:
        for item in list_el:
            if _local(item.tag) != "li":
                continue
            label_el = None
            link_el = None
            for child in item:
                local = _local(child.tag)
                if local in {"a", "span"} and label_el is None:
                    label_el = child
                if local == "a" and link_el is None:
                    link_el = child
            title = ""
            if label_el is not None:
                title = _clean_text_line("".join(label_el.itertext()))
            if link_el is not None and link_el.get("href"):
                path, fragment = _resolve(
                    base_dir, link_el.get("href") or "", nav_path
                )
                if path:
                    entries.append(
                        _TocEntry(
                            title=title or posixpath.basename(path),
                            href=path,
                            fragment=fragment,
                            level=level,
                        )
                    )
            nested = next(
                (child for child in item if _local(child.tag) == "ol"), None
            )
            if nested is not None:
                walk(nested, level + 1)

    list_el = next((child for child in nav_el if _local(child.tag) == "ol"), None)
    if list_el is not None:
        walk(list_el, 1)
    return entries


def _parse_ncx(data: bytes, ncx_path: str) -> list[_TocEntry]:
    root = _parse_xml(data, ncx_path)
    nav_map = _find_descendant(root, "navmap")
    if nav_map is None:
        return []

    entries: list[_TocEntry] = []
    base_dir = posixpath.dirname(ncx_path)

    def walk(point: ET.Element, level: int) -> None:
        label_el = _find_descendant(point, "navlabel")
        title = ""
        if label_el is not None:
            text_el = _find_descendant(label_el, "text")
            if text_el is not None:
                title = _clean_text_line("".join(text_el.itertext()))
        content_el = next(
            (child for child in point if _local(child.tag) == "content"), None
        )
        if content_el is not None and content_el.get("src"):
            path, fragment = _resolve(
                base_dir, content_el.get("src") or "", ncx_path
            )
            if path:
                entries.append(
                    _TocEntry(
                        title=title or posixpath.basename(path),
                        href=path,
                        fragment=fragment,
                        level=level,
                    )
                )
        for child in point:
            if _local(child.tag) == "navpoint":
                walk(child, level + 1)

    for child in nav_map:
        if _local(child.tag) == "navpoint":
            walk(child, 1)
    return entries


def _find_descendant(element: ET.Element, name: str) -> ET.Element | None:
    for child in element.iter():
        if _local(child.tag) == name:
            return child
    return None


class _TextBuilder:
    def __init__(self) -> None:
        self.parts: list[str] = []
        self.length = 0
        self.tail = ""

    def append(self, text: str) -> None:
        if not text:
            return
        self.parts.append(text)
        self.length += len(text)
        self.tail = (self.tail + text)[-4:]

    def newline(self) -> None:
        if self.length == 0 or self.tail.endswith("\n"):
            return
        self.append("\n")

    def blankline(self) -> None:
        if self.length == 0 or self.tail.endswith("\n\n"):
            return
        if self.tail.endswith("\n"):
            self.append("\n")
        else:
            self.append("\n\n")

    def text(self) -> str:
        return "".join(self.parts)


def _normalize_text(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\xa0", " ")
    text = INVISIBLE_RE.sub("", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"(?<=[一-鿿])\s+(?=[一-鿿])", "", text)
    text = re.sub(r"\s+(?=[，。！？；：、）】」』,.!?;:])", "", text)
    text = re.sub(r"(?<=[（【「『])\s+", "", text)
    return text


def _clean_text_line(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(text or "")).strip()


class _TextExtractor(HTMLParser):
    """把 XHTML 正文转成纯文本，同时记录 id 锚点和标题偏移。"""

    def __init__(self, read_alt: bool = False) -> None:
        super().__init__(convert_charrefs=True)
        self.read_alt = read_alt
        self.builder = _TextBuilder()
        self.anchors: dict[str, int] = {}
        self.headings: list[tuple[int, str, int]] = []
        self.excluded = False
        self.skip_stack: list[str] = []
        self.heading_stack: list[tuple[int, int, list[str]]] = []
        self.table_depth = 0
        self.cells_in_row = 0
        self.title_parts: list[str] = []
        self.in_title = False
        self.link_depth = 0
        self.link_chars = 0

    def handle_starttag(self, tag: str, attrs_list) -> None:
        tag = tag.lower()
        attrs = {key.lower(): (value or "") for key, value in attrs_list}

        if tag == "title":
            self.in_title = True
            self.title_parts = []
        if self.skip_stack:
            if tag in SKIP_TAGS or tag == self.skip_stack[-1]:
                self.skip_stack.append(tag)
            return
        if tag in SKIP_TAGS:
            self.skip_stack.append(tag)
            return

        epub_types = _parse_epub_types(attrs)
        if "pagebreak" in epub_types:
            self.skip_stack.append(tag)
            return
        if tag == "aside" and epub_types & {"footnote", "endnote", "rearnote"}:
            self.skip_stack.append(tag)
            return
        if tag == "body" and epub_types & EXCLUDED_TYPES:
            self.excluded = True
            self.skip_stack.append(tag)
            return

        if tag == "br":
            self.builder.newline()
            return
        if tag == "hr":
            self.builder.blankline()
            return
        if tag in {"img", "image"}:
            if self.read_alt:
                alt = _normalize_text(attrs.get("alt", ""))
                self.builder.append(alt)
            return
        if tag == "table":
            self.table_depth += 1
            self.builder.blankline()
            return
        if self.table_depth:
            if tag == "tr":
                self.builder.newline()
                self.cells_in_row = 0
            elif tag in {"td", "th"}:
                if self.cells_in_row:
                    self.builder.append(" | ")
                self.cells_in_row += 1
            return

        if tag == "a":
            self.link_depth += 1

        is_heading = bool(HEADING_RE.match(tag))
        if is_heading or tag in BLOCK_TAGS:
            self.builder.blankline()

        for attr_name in ("id", "name"):
            value = attrs.get(attr_name)
            if value:
                self.anchors.setdefault(value, self.builder.length)

        if is_heading:
            self.heading_stack.append(
                (int(tag[1]), self.builder.length, [])
            )

    def handle_startendtag(self, tag: str, attrs_list) -> None:
        self.handle_starttag(tag, attrs_list)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title":
            self.in_title = False
        if self.skip_stack:
            if self.skip_stack[-1] == tag:
                self.skip_stack.pop()
            return

        if tag == "table":
            if self.table_depth:
                self.table_depth -= 1
            self.builder.blankline()
            return
        if self.table_depth:
            if tag == "tr":
                self.builder.newline()
            return

        if tag == "a" and self.link_depth:
            self.link_depth -= 1

        if HEADING_RE.match(tag):
            if self.heading_stack:
                level, offset, parts = self.heading_stack.pop()
                title = _normalize_text("".join(parts)).strip()
                if title:
                    self.headings.append((level, title, offset))
            self.builder.blankline()
            return
        if tag in BLOCK_TAGS:
            self.builder.blankline()

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)
            return
        if self.skip_stack:
            return
        text = _normalize_text(data)
        if not text:
            return
        self.builder.append(text)
        if self.link_depth:
            self.link_chars += len(text)
        for _level, _offset, parts in self.heading_stack:
            parts.append(text)

    def document_title(self) -> str | None:
        title = _clean_text_line("".join(self.title_parts))
        return title or None


def _decode_markup(data: bytes) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig", errors="replace")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    for encoding in ("utf-8", "gb18030", "big5"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _extract_document(data: bytes) -> _Document:
    markup = _decode_markup(data)
    extractor = _TextExtractor()
    try:
        extractor.feed(markup)
        extractor.close()
        text = extractor.builder.text()
        return _Document(
            text=text,
            anchors=extractor.anchors,
            headings=extractor.headings,
            excluded=extractor.excluded,
            title=extractor.document_title(),
            link_chars=extractor.link_chars,
        )
    except Exception:
        # 极端畸形的 XHTML 兜底：去标签后当整章
        cleaned = re.sub(r"<[^>]+>", " ", markup)
        cleaned = _normalize_text(html.unescape(cleaned))
        title_match = re.search(
            r"<title[^>]*>(.*?)</title>", markup, re.IGNORECASE | re.DOTALL
        )
        title = (
            _clean_text_line(re.sub(r"<[^>]+>", "", title_match.group(1)))
            if title_match
            else None
        )
        return _Document(cleaned, {}, [], False, title)


def _select_level(toc: list[_TocEntry], chapter_level: str | int) -> int:
    if isinstance(chapter_level, int):
        return max(1, chapter_level)
    if chapter_level not in (None, "", "auto"):
        try:
            return max(1, int(str(chapter_level)))
        except ValueError as exc:
            raise EpubError(f"无效的 chapter_level：{chapter_level}") from exc
    if not toc:
        return 1
    counts = Counter(entry.level for entry in toc)
    candidates = [level for level, count in counts.items() if count <= 300]
    return max(candidates) if candidates else max(counts)


def _choose_heading_level(headings: list[tuple[int, str, int]]) -> int | None:
    counts = Counter(level for level, _title, _offset in headings)
    for level in range(1, 7):
        if counts[level] >= 2:
            return level
    return None


def _skip_by_guide(document: _Document, guide_type: str) -> bool:
    """guide 标为非正文时，只跳过真正的短页/链接页，保留夹带正文的大文件。"""
    text_length = len(document.text.strip())
    if text_length < 2000:
        return True
    link_ratio = document.link_chars / max(1, len(document.text))
    if link_ratio > 0.4:
        return True
    if guide_type in {"cover", "titlepage", "title-page", "copyright-page"}:
        return text_length < 10000
    if guide_type in {"index", "colophon"}:
        return True
    # toc：长度大且链接占比低，说明这个 XHTML 同时装了正文，不能整份跳过
    return False


def _speakable(text: str) -> bool:
    if len(text) < 20:
        return False
    letters = sum(1 for char in text if char.isalnum())
    return letters >= 10 and letters / max(1, len(text)) >= 0.2


def _clean_chapter_text(text: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _chapter_key(href: str, title: str, text: str) -> str:
    payload = f"{href}|{title}|{text}".encode("utf-8")
    return hashlib.sha1(payload).hexdigest()


def _plan_chapters(
    book: _Book,
    documents: dict[str, _Document],
    chapter_level: str | int,
) -> list[Chapter]:
    selected_level = _select_level(book.toc, chapter_level)
    entries_by_path: dict[str, list[_TocEntry]] = {}
    for entry in book.toc:
        entries_by_path.setdefault(entry.href, []).append(entry)

    chapters: list[Chapter] = []
    for href in book.spine_paths:
        document = documents.get(href)
        if document is None or document.excluded:
            continue

        entries = entries_by_path.get(href, [])
        chosen = [entry for entry in entries if entry.level == selected_level]
        if not chosen:
            chosen = entries

        anchor_pairs: list[tuple[int, _TocEntry]] = []
        seen_offsets: set[int] = set()
        for entry in chosen:
            if entry.fragment and entry.fragment in document.anchors:
                offset = document.anchors[entry.fragment]
                if offset not in seen_offsets:
                    seen_offsets.add(offset)
                    anchor_pairs.append((offset, entry))
        anchor_pairs.sort(key=lambda pair: pair[0])

        drop_prefix = book.guide.get(href) == "toc"
        segments: list[tuple[str, str, int]] = []
        if len(anchor_pairs) >= 2:
            for position, (offset, entry) in enumerate(anchor_pairs):
                end = (
                    anchor_pairs[position + 1][0]
                    if position + 1 < len(anchor_pairs)
                    else len(document.text)
                )
                segment = document.text[offset:end]
                if position == 0 and not drop_prefix:
                    segment = document.text[:offset] + segment
                segments.append(
                    (entry.title or posixpath.basename(href), segment, entry.level)
                )
        elif len(anchor_pairs) == 1:
            entry = anchor_pairs[0][1]
            offset = anchor_pairs[0][0]
            segment = document.text[offset:] if drop_prefix else document.text
            segments.append(
                (entry.title or posixpath.basename(href), segment, entry.level)
            )
        else:
            heading_level = _choose_heading_level(document.headings)
            if heading_level is not None:
                heading_items = [
                    heading for heading in document.headings if heading[0] == heading_level
                ]
                heading_items.sort(key=lambda heading: heading[2])
                for position, (level, title, offset) in enumerate(heading_items):
                    end = (
                        heading_items[position + 1][2]
                        if position + 1 < len(heading_items)
                        else len(document.text)
                    )
                    segment = document.text[offset:end]
                    if position == 0 and not drop_prefix:
                        segment = document.text[:offset] + segment
                    segments.append((title, segment, level))
            else:
                fallback_title = (
                    entries[0].title
                    if entries
                    else (document.headings[0][1] if document.headings else "")
                ) or document.title or ""
                segments.append((fallback_title, document.text, 1))

        for title, segment, level in segments:
            text = _clean_chapter_text(segment)
            if not _speakable(text):
                continue
            index = len(chapters) + 1
            clean_title = _clean_text_line(title) or f"第{index}章"
            chapters.append(
                Chapter(
                    index=index,
                    title=clean_title,
                    level=level,
                    href=href,
                    fragment=None,
                    text=text,
                    source_key=_chapter_key(href, clean_title, text),
                )
            )

    return chapters
