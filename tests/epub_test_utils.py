"""测试用最小 EPUB 生成器（标准库，不联网）。"""

from __future__ import annotations

import posixpath
import zipfile
from xml.sax.saxutils import escape, quoteattr

CONTAINER = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

OPS_NS = "http://www.idpf.org/2007/ops"


def _render_nav_list(entries: list[dict]) -> str:
    out = ["<ol>"]
    for entry in entries:
        out.append("<li>")
        if entry.get("href"):
            out.append(
                f'<a href={quoteattr(entry["href"])}>{escape(entry["title"])}</a>'
            )
        else:
            out.append(f"<span>{escape(entry['title'])}</span>")
        children = entry.get("children") or []
        if children:
            out.append(_render_nav_list(children))
        out.append("</li>")
    out.append("</ol>")
    return "".join(out)


def _render_ncx(entries: list[dict], counter: list[int]) -> str:
    out: list[str] = []
    for entry in entries:
        counter[0] += 1
        number = counter[0]
        out.append(
            f'<navPoint id="np{number}" playOrder="{number}">'
            f"<navLabel><text>{escape(entry['title'])}</text></navLabel>"
            f'<content src={quoteattr(entry.get("href", ""))}/>'
        )
        children = entry.get("children") or []
        if children:
            out.append(_render_ncx(children, counter))
        out.append("</navPoint>")
    return "".join(out)


def build_epub(
    path: str,
    *,
    files: dict[str, str],
    spine: list[str | tuple[str, str]],
    toc: list[dict] | None = None,
    version: int = 3,
    title: str = "测试书",
    author: str = "测试作者",
    language: str = "zh",
    guide: list[tuple[str, str]] | None = None,
    cover: tuple[str, bytes] | None = None,
) -> str:
    """生成一个最小 EPUB。

    files: OEBPS 下的 xhtml 文件，name -> 内容
    spine: 文件名列表，或 (文件名, linear) 元组
    toc: 嵌套结构 [{"title":..., "href":..., "children":[...]}]
    """
    toc = toc or []
    spine_entries: list[tuple[str, str]] = []
    for entry in spine:
        if isinstance(entry, tuple):
            spine_entries.append((entry[0], entry[1]))
        else:
            spine_entries.append((entry, "yes"))

    manifest_items: list[str] = []
    file_ids: dict[str, str] = {}
    for index, name in enumerate(files, start=1):
        item_id = f"doc{index}"
        file_ids[name] = item_id
        manifest_items.append(
            f'<item id="{item_id}" href={quoteattr(name)} media-type="application/xhtml+xml"/>'
        )

    nav_name = "nav.xhtml"
    ncx_name = "toc.ncx"
    if version == 3:
        manifest_items.append(
            f'<item id="nav" href="{nav_name}" '
            f'media-type="application/xhtml+xml" properties="nav"/>'
        )
    else:
        manifest_items.append(
            f'<item id="ncx" href="{ncx_name}" '
            f'media-type="application/x-dtbncx+xml"/>'
        )

    if cover is not None:
        cover_name, _data = cover
        suffix = posixpath.splitext(cover_name)[1].lower()
        media_type = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".gif": "image/gif",
            ".webp": "image/webp",
        }.get(suffix, "image/jpeg")
        manifest_items.append(
            f'<item id="cover-image" href={quoteattr(cover_name)} '
            f'media-type="{media_type}" properties="cover-image"/>'
        )

    spine_xml = "".join(
        f'<itemref idref="{file_ids[name]}"'
        + ("" if linear == "yes" else ' linear="no"')
        + "/>"
        for name, linear in spine_entries
    )
    guide_xml = ""
    if guide:
        references = "".join(
            f"<reference type={quoteattr(kind)} href={quoteattr(href)}/>"
            for kind, href in guide
        )
        guide_xml = f"<guide>{references}</guide>"

    toc_attribute = ' toc="ncx"' if version == 2 else ""
    opf = f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="{version}.0" unique-identifier="book-id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="book-id">test-book</dc:identifier>
    <dc:title>{escape(title)}</dc:title>
    <dc:creator>{escape(author)}</dc:creator>
    <dc:language>{escape(language)}</dc:language>
  </metadata>
  <manifest>
    {''.join(manifest_items)}
  </manifest>
  <spine{toc_attribute}>
    {spine_xml}
  </spine>
  {guide_xml}
</package>
"""

    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            zipfile.ZipInfo("mimetype"),
            "application/epub+zip",
            compress_type=zipfile.ZIP_STORED,
        )
        archive.writestr("META-INF/container.xml", CONTAINER)
        archive.writestr("OEBPS/content.opf", opf)
        for name, content in files.items():
            archive.writestr(f"OEBPS/{name}", content)
        if version == 3:
            nav = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<html xmlns="http://www.w3.org/1999/xhtml" '
                f'xmlns:epub="{OPS_NS}">'
                "<head><title>目录</title></head><body>"
                '<nav epub:type="toc">'
                + _render_nav_list(toc)
                + "</nav></body></html>"
            )
            archive.writestr(f"OEBPS/{nav_name}", nav)
        else:
            counter = [0]
            ncx = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
                "<head/><docTitle><text>"
                + escape(title)
                + "</text></docTitle><navMap>"
                + _render_ncx(toc, counter)
                + "</navMap></ncx>"
            )
            archive.writestr(f"OEBPS/{ncx_name}", ncx)
        if cover is not None:
            archive.writestr(f"OEBPS/{cover[0]}", cover[1])
    return path


def xhtml(body: str, title: str = "") -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        f'xmlns:epub="{OPS_NS}">'
        f"<head><title>{escape(title)}</title></head><body>{body}</body></html>"
    )
