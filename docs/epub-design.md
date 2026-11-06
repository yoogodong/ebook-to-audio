# 电子书转有声书设计方案（草案）

> 状态：EPUB 支持已实现（`core/epub_processor.py`、`core/epub_converter.py`，CLI/GUI 已接入）；m4b 见第 11 节。
>
> 目标：把 epub 拆成有序、可读的章节文本，复用现有 `core/tts_processor.py` 的分块合成、重试、逐块缓存与续传，不做第二套 TTS 逻辑；输出支持 mp3 与 m4b（m4b 需要系统 ffmpeg，见第 11 节）。

## 结论摘要

- **怎么切**：以 `spine` 的顺序为骨架，TOC（EPUB3 `nav` 优先，EPUB2 `NCX` 兜底）只负责标题与层级，XHTML 内的 `h1–h6` 作为兜底。默认输出带章节标记的 m4b + 每章 mp3；可选合并 mp3。
- **怎么喂 TTS**：每章清洗成纯文本后，逐章调用现有 `convert_text_to_audio`。900 字切块、重试、缓存、续传、合并全部复用；不要为 epub 再写一套切块逻辑。
- **依赖**：epub 解析优先标准库（`zipfile` + `xml.etree.ElementTree` + `html.parser`），不引入 EbookLib/lxml；m4b 导出需要系统 `ffmpeg`/`ffprobe`。

## 1. EPUB 内部结构（最小必要知识）

- **ZIP/OCF**：epub 就是一个 zip。规范要求 `mimetype` 是压缩包第一个文件、使用 `ZIP_STORED` 不压缩、无 extra field，内容为 `application/epub+zip`（EPUB 3.3 §4.3.2）。读取端不必因此拒绝不合规文件，`META-INF/container.xml` 才是真正的入口；但测试夹具应当合规。
- **container.xml**：指向 OPF，`rootfile@full-path` 相对 zip 根目录，`media-type="application/oebps-package+xml"`。可能有多个 rootfile，取第一个可用的。
- **OPF**：`metadata`（`dc:title`、`dc:creator`、`dc:language`）、`manifest`（`item@id/href/media-type/properties`）、`spine`（`itemref@idref/linear`），EPUB2 还有 `guide`。**manifest 的顺序不是阅读顺序，spine 才是**（EPUB 3.3 §5.7.1 “default reading order”）；`linear="no"` 是辅助内容（注释、答案等）。
- **EPUB2 目录 = NCX**：`toc.ncx`，media-type `application/x-dtbncx+xml`，由 `spine@toc` 指向 manifest id。`navMap` 必需，嵌套 `navPoint`，每个含 `navLabel/text` 与 `content@src`（`文件#片段`）。`playOrder` 可省略，**不要用它排序**，用文档顺序。
- **EPUB3 目录 = nav.xhtml**：manifest 中 `properties="nav"` 的 XHTML，含一个 `<nav epub:type="toc">`；结构是 `ol > li > (a|span) + 嵌套 ol`。`a` 是可跳转项，`span` 是分组标题（如“第一卷”）。nav 文档**不强制出现在 spine 里**。
- **两者关系**：EPUB3 常同时带 NCX 兼容旧阅读器，**优先 nav，失败再 NCX**。TOC 顺序与 spine 一致只是 SHOULD，**冲突时永远信 spine**，TOC 只提供标注。
- **非正文语义**：EPUB3 `epub:type` 常见 `frontmatter/backmatter/chapter/cover/titlepage/copyright-page/toc/pagebreak/noteref/footnote/endnote`；EPUB2 `guide@type` 有 `cover/title-page/toc/index/glossary/colophon/copyright-page/foreword/preface/notes`。`guide` 只是建议，阅读系统可以不理会。
- **加密/版式**：`META-INF/encryption.xml`、`rights.xml`；`rendition:layout=pre-paginated` 表示固定版式。

## 2. 章节划分

| 方案 | 优点 | 缺点 | 适合的输出粒度 |
|---|---|---|---|
| 按 spine item | 阅读顺序有保证；实现最简单；无 TOC 也能用 | 粒度极不稳定：有的书一章一文件，有的全书一个文件、每场景一个文件；标题要另找 | 每章一个 mp3（默认骨架） |
| 按 TOC 层级 | 标题、卷/章层级最准；可选择“章”而不是“节” | TOC 可能缺失、重复、与 spine 不一致；按片段锚点切分要额外做偏移映射 | 按 TOC 层级决定文件粒度 |
| 按 XHTML 内 h1–h6 | 能处理“整本书一个文件”；无需 TOC | 会把章内小节误当章；标题层级不稳定；装饰性标题会误切 | 每章一个 mp3（兜底） |
| 整本书一个流 | 实现最省事；连续收听体验好 | 无章节进度/导航；失败重跑粒度粗；单文件巨大 | 全书一个 mp3（最后兜底） |

**推荐混合算法**（不要四选一）：

1. 读 spine，按顺序保留 `linear != "no"` 的 XHTML；跳过 `properties="nav"` 的导航文档。
2. 读 TOC：nav → NCX → 空。解析成 `(title, path, fragment, level)`；href 分别相对 nav 文档/NCX 文档解析（不是相对 OPF），先 URL decode，按 `(path, fragment)` 去重。
3. 选择“章”的层级：允许 `--chapter-level N`；auto 规则建议“在条目数 ≤ 300 的最深层级里选最深的一层，若该层中位正文长度 < ~1500 字则退回上一层”，避免把“一、二、三”小节当章。
4. 逐个 spine item：
   - 该文件有 ≥2 个所选层级的 TOC 锚点 → 按锚点在清洗后文本中的字符偏移切分；
   - 只有 1 个或没有锚点 → 若文件内有 ≥2 个同级标题（优先 `epub:type="chapter"`，其次 h1/h2），按标题切；否则整文件一章。
5. 按 spine 位置、再按锚点位置排序；TOC 只提供标题，**绝不按 TOC 重排 spine**。未被 TOC 覆盖的 spine 文本补成 fallback 章，避免丢内容。
6. 标题优先级：TOC label > 首个标题 > `<title>` > 生成“第 N 章”。

**用户可能没想到的几点**：

- **TOC 层级决定文件粒度**：`span` 分组（卷/部）不是章节，不应生成 mp3。两级结构建议文件名 `003_第二卷_第五章_风起.mp3`；MVP 先扁平，`--volume-dirs` 以后再加。全书合并按“深度优先展开后的 spine 顺序”，不是按目录树。
- **非正文过滤**：优先用结构信号——`linear="no"`、`properties="nav"`、`guide@type`、`epub:type`。默认跳过 cover/titlepage/copyright-page/toc/index/colophon；保留 preface/foreword/introduction/prologue/epilogue/appendix/afterword（可配置）。关键词（“目录”“版权”“Copyright”）只做兜底，且仅在章节很短或正文为空时生效，避免误杀《目录学》这类书。
- **超长章节的“二级切分”不是 TTS 需要**：`split_text` 已按 900 字切块，章节再长也能合成。把一章拆成多个 mp3 只是产品决策（文件大小、导航、按段重试）。默认不预切；只有超过 `--max-chapter-chars` 时才在标题/段落边界切成 `第X章_002.mp3`，不要按 900 字切输出文件。
- **章节标题是否朗读**：每章一个 mp3 时，标题在文件名/元数据里即可，默认**不朗读**；全书合并时听众才需要章节播报。`--read-title` 可在每章正文前加“第一章，标题。”，并删掉正文里重复的首标题。更精细的做法是标题音频只在全书合并时插入，复杂度高，非 MVP。
- **边界识别失败的降级链**：TOC 缺失/损坏 → spine + 标题；TOC 与 spine 不一致 → 信 spine；锚点找不到 → 退化为整文件，多个条目折叠时合并标题；spine 只有一个大文件 → 标题启发式；连标题也没有 → 按段落切成约 2 万字的部分；全部失败 → 整本一个流，manifest 里记 warning。

## 3. 文本清洗

| 对象 | 处理 |
|---|---|
| script/style/noscript/template/head | 整个子树跳过（按标签计数，防嵌套/未闭合） |
| 注释、CDATA | 忽略注释；CDATA 当普通文本 |
| 段落/换行 | `p/div/section/article/li/tr/h1-h6/blockquote` 等块级元素前后置 `\n\n`，`br` 置 `\n`；行内元素不额外加空格。段内空白折叠为一个空格，段间保留空行 |
| 中文空格 | 折叠后删除两个 CJK 字符之间的空白；删除标点前空格。**不要依赖换行制造停顿**：`Communicate` 接收纯文本并内部 escape，不能喂 SSML，停顿主要由标点决定 |
| HTML 实体 | `HTMLParser(convert_charrefs=True)`；`&nbsp;` → 空格，数字实体正常解码 |
| ruby 注音 | 跳过 `rt/rp`，保留 `rb`/基字 |
| 脚注/尾注 | 默认跳过 `epub:type` 为 `footnote/endnote` 的 `aside`；`noteref` 只有文本像标记（数字、`*`、`†`、`[1]`）时才跳过，避免误删被链接包裹的正文。`--notes append` 在章末追加“注释：…” |
| 图片 | 默认读 `figcaption`，不读 `alt`（alt 常是文件名/“cover”）；仅当本章除此以外为空，或显式 `--read-alt` 时才读 alt |
| 表格 | 压平成“表头: 单元格”逐行文本，行间 `\n`；或配置跳过 |
| 标点/数字 | 弯引号→标准引号，破折号/省略号规范化（等价 Calibre 的 `--smarten-punctuation`）；日期、罗马数字、单位读数作为后续增强 |
| OCR | 纯图片章节可选 OCR（pytesseract+PIL），默认关闭；清洗后正文为空时提示“可能需 OCR”，不要静默生成空章 |
| 页码/分页符 | 删除 `epub:type="pagebreak"` 元素；只删 class/id 含 `page` 且整行仅为数字/罗马数字的段落，避免误删正文数字 |
| 不可见字符 | 删 `U+0000–001F`（保留 `\n`）、`007F–009F`、`200B–200F`、`202A–202E`、`2060–2064`、`FEFF`、`00AD`；`U+3000` → 空格 |
| 全角/半角 | **不要全局 NFKC**：NFKC 会把中文全角逗号句号转成 ASCII，破坏中文停顿；只做定向替换，保留中文标点 |
| 命名空间 | 用 local-name 匹配，不硬编码前缀；XHTML 默认命名空间下 `html.parser` 的标签名就是普通 `p`，`epub:type` 属性名保留冒号 |

“清洗后正文”判定规则（结构过滤之后）：

```python
def speakable(text: str) -> bool:
    t = text.strip()
    letters = sum(ch.isalnum() for ch in t)  # Python 的 isalnum 对中文为 True
    return len(t) >= 30 and letters >= 20 and letters / max(1, len(t)) >= 0.3
```

再加一条“疑似目录页”只告警、不自动删：非空行 ≥5、多数行很短且以数字结尾。真正的目录页应优先靠 `properties="nav"`/`epub:type="toc"` 过滤。

## 4. 输出组织与断点续传

建议目录：

```
book.mp3                     # 全书合并（-o 指定）
book.m4b                     # 可选：带章节标记/元数据/封面的有声书
book_chapters/
  001_第一章_风起.mp3
  002_第二章_....mp3
  manifest.json
```

- **命名**：`{index:0{width}d}_{slug}.mp3`，width 至少 3；slug 只保留 CJK/字母/数字，其余（含 `/ \ : * ? " < > |`、空白、控制符）替换为 `_`，截断到约 40 字符，空标题回退 `chapter`。索引前缀天然解决重名、Windows 保留名、大小写不敏感碰撞；不要用标题单独做文件名。
- **每章独立缓存**：直接以章节 mp3 路径调用 `convert_text_to_audio`，core 会生成 `<章节>.mp3.parts/`，成功自动删、失败保留。**不要拿整本书调用一次**，否则只有一个巨大 `.parts`，失去按章续传。
- **manifest.json**：记录源文件 `sha1/size/mtime`、`voice/rate/volume/pitch` 指纹、章节列表（index/title/href/fragment/正文 sha1/输出路径/status）。只有“源未变 + 参数未变 + 输出存在且正文 sha1 匹配”才跳过；换音色必须全部重生成；标题变化导致输出改名时清理旧文件。
- **全书合并**：按 manifest 顺序流式拼接，写 `<output>.part` 后 `os.replace`。只有全部章节成功才合并；可选 `--allow-partial`。m4b 导出见第 11 节。
- **关于 ID3 的实测结论**：仓库现有的 `chapter.mp3`、`Zhengjian.mp3` 都以 MPEG 帧同步 `FF F3` 开头，扫描未发现合法的 ID3v2 头（文件里出现的 `ID3` 字节是音频数据中的偶然序列）。edge-tts 7.x（当前锁定 7.3.1）的输出是 `audio-24khz-48kbitrate-mono-mp3`，逐块二进制拼接在解码层可用。**不要把“多段会带多个 ID3 头”当设计前提**。仍建议防御：合并时若发现某段以合法 `ID3` 开头就剥离；将来若写 ID3，只写最终全书文件。真正的隐患是每段独立编码的 encoder delay/padding，拼接后可能有极短的 gap/click；要精确 seek/时长就需要 ffmpeg/mutagen 重封装，非 MVP。章节之间也没有自动静音，靠段末标点自然停顿。

## 5. 依赖与库选择

| 方案 | 新增依赖/体积 | 评价 |
|---|---|---|
| **标准库** `zipfile` + `ElementTree` + `html.parser` | 0 | 推荐。OPF/NCX 用 ElementTree（命名空间/local-name）；正文和 nav 用 `html.parser`（容错、能记录锚点偏移）。OPF 若 XML 不合法，ET 会失败，需正则兜底 |
| `EbookLib` | 依赖 `lxml`；cp313 macOS universal2 轮子约 8MB | 维护活跃，能直接给 `book.spine` 与嵌套 `book.toc`，EPUB2 无 nav 时会读 NCX（`ignore_ncx` 默认 True）；但仍要自己清洗正文、算锚点偏移，收益有限 |
| `beautifulsoup4` | `soupsieve` + `typing-extensions`（约 160KB，纯 Python） | 容错好，但不提供 OPF/spine/TOC，仍要 XML 解析，对本题是多余一层 |
| `lxml` | 约 4–8MB 编译扩展 | `recover=True` 对破损 XHTML 很强，但为一个只读工具引入 C 扩展不划算；除非实测破损率很高 |

**推荐：标准库核心 + 可选 EbookLib**。先写零依赖版本；若后续发现大量畸形 OPF/NCX 维护成本高，再把 EbookLib 作为 `epub` extra，而不是默认依赖。

## 6. 模块与 API 设计

新增 `src/ebook_to_audio/core/epub_processor.py`：

```python
@dataclass(frozen=True)
class Chapter:
    index: int              # 输出顺序，1 起
    title: str
    level: int              # TOC 深度，1 起
    href: str               # zip 内路径
    fragment: str | None    # 锚点；None 表示整文件
    text: str
    source_key: str         # sha1(href|fragment|text)
    part: int = 1
    parts_total: int = 1


def read_epub(path: str) -> EpubBookMeta: ...
def plan_chapters(meta, chapter_level: str | int = "auto") -> list[ChapterRef]: ...
def extract_text(epub_path, href, fragment=None, *,
                 notes: str = "skip", read_alt: bool = False) -> str: ...
def convert_epub_file(input_file, output_file=None, *, voice=None,
                      rate="+0%", volume="+0%", pitch="+0Hz",
                      chapter_level="auto", max_chapter_chars=None,
                      read_title=False, keep_chapters=True,
                      progress=None, cancel_check=None,
                      retries=3) -> EpubResult: ...
def convert_file(input_file, output_file=None, **kwargs): ...  # .txt/.epub 分派
```

关键伪代码：

```python
# plan：spine 定序，TOC 定标题
for path in spine_paths:                       # 已跳过 linear=no / nav
    entries = dedupe(toc_by_path.get(path, []), key=(path, fragment))
    if len(entries) >= 2:
        text, anchors = extract_with_offsets(path)      # id -> 清洗后字符偏移
        pts = sorted((anchors[e.fragment], e)
                     for e in entries if e.fragment in anchors)
        for i, (off, e) in enumerate(pts):
            end = pts[i + 1][0] if i + 1 < len(pts) else len(text)
            seg = text[off:end]
            if i == 0:
                seg = text[:off] + seg           # 首个锚点前的文本并入
            yield Chapter(title=e.title, level=e.level, text=seg, ...)
    else:
        yield from split_by_headings_or_whole(path, entries)

# 进度：按章加权，避免一个大章长时间显示 1%
weights = [max(len(ch.text), 1) for ch in chapters]
overall = (sum(weights[:i]) + weights[i] * p / 100) / sum(weights) * 100
```

**CLI**：`main.py` 按后缀分派；新增 `--chapter-level`、`--no-merge`、`--no-chapters`、`--max-chapter-chars`、`--read-title`、`--notes {skip,append}`。`-o` 仍表示全书 mp3；章节目录默认 `splitext(output)[0] + "_chapters"`。

**GUI**：文件对话框/拖放加 `.epub`；`update_file_info` **不能**再对 epub 调 `read_file`（zip 不是 UTF-8 文本），改调 `inspect_epub` 显示书名/作者/章数；`ConvertThread` 改为接收“输入路径”并调用 `convert_file`，不要在 UI 线程预读正文。

**输出规则**：txt 与 epub 默认都输出 m4b；epub 同时保留 `<stem>_chapters/` 章节 mp3，`-f both` 额外生成 `<stem>.mp3`，`--no-chapters` 在 m4b 成功后删除章节 mp3。

## 7. 测试策略

用标准库在 `setUp` 里生成 epub，不提交二进制夹具：

```python
def make_epub(path, version=3, spine=..., toc=..., files=...):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip",
                   compress_type=zipfile.ZIP_STORED)     # 必须是第一个
        z.writestr("META-INF/container.xml", CONTAINER)
        z.writestr("OEBPS/content.opf", opf)
        if version == 2:
            z.writestr("OEBPS/toc.ncx", ncx)
        else:
            z.writestr("OEBPS/nav.xhtml", nav)
        for name, data in files.items():
            z.writestr(f"OEBPS/{name}", data)
```

覆盖用例：EPUB2 只有 NCX（两层嵌套）；EPUB3 只有 nav 且 nav 不在 spine；无 TOC；TOC 与 spine 顺序/成员不一致；同一文件多锚点切分；重复 TOC 条目；单章超长；空章节；纯图片章（有/无 alt）；卷/章嵌套；GB18030 声明；破损未闭合 XHTML；ruby/脚注/pagebreak；非法标题字符与重名；`linear="no"`；DRM `encryption.xml`；fixed-layout；取消/续传；换音色使 manifest 失效。合成层继续用现有 `FakeSynth` 的 `patch("...synthesize_chunk")` 方式，测试不联网。

## 8. 风险清单

| 风险 | 建议 |
|---|---|
| DRM/加密 | 解析 `encryption.xml`：若加密资源包含 spine 的 XHTML，直接报“受 DRM 保护，无法转换”；只有字体混淆则忽略。`rights.xml`/`license.lcpl` 存在时提示 ADEPT/LCP，绝不尝试破解 |
| fixed-layout/漫画 | `rendition:layout=pre-paginated` 或 Apple `fixed-layout` meta：不要按 spine item 一页一章；先尝试 SVG `<text>`，文本仍不足则明确拒绝或提示“需 OCR” |
| 超大书内存 | 逐 spine item 读取/清洗/合成，不同时把所有 XHTML 放内存；合并流式复制；对单个成员解压大小设上限 |
| 重复 TOC 条目 | 按 `(规范化 path, fragment)` 去重，保留首次；同标题指向不同目标则都保留；日志输出被合并项 |
| 非 UTF-8 声明 | 依次看 XML declaration、BOM、`meta charset`；尝试 utf-8 → gb18030 → big5；失败要告警，不能静默 `errors="ignore"` |
| 标题导致非法文件名 | slug + 固定宽度索引前缀 + 截断；NFC 规范化；索引前缀同时规避 Windows 保留名 |
| 多语言书 | `dc:language` 只用于“用户未显式指定 voice 时”的自动选音色；混合语言逐段切 voice 非 MVP |
| Gutenberg 式结构 | `toc.xhtml` 常出现在 spine，首尾有 PG 头尾注；靠 `properties`/`guide`/`epub:type` 过滤，关键词只兜底；`div.chapter` 可作为标题切分信号 |
| 路径/命名空间/大小写 | href 先 `unquote` 再 `posixpath.normpath`，拒绝 `..`；manifest 的 href 相对 OPF 目录，nav/NCX 的链接相对各自文档；zip 名大小写不匹配时做一次大小写不敏感查找 |
| XML 安全/zip bomb | ElementTree 不主动取外部 DTD，但仍有实体膨胀风险；限制 XML 大小，必要时再引入 `defusedxml`（不默认加依赖） |
| 部分成功就合并 | 默认全部章节成功才合并全书；`--allow-partial` 显式开启，manifest 标记缺失章 |
| 无 ID3/无静音 | 当前 edge-tts 产物无合法 ID3，二进制拼接可用；不要为 ID3 引入复杂度。章节间无静音，确保段末有标点即可；精确封装/seek 再考虑 ffmpeg/mutagen |

## 9. 最小可用版本（MVP）排序

1. **先做 spine-only**：`read_epub()` 解析 container/OPF/spine，`html.parser` 清洗正文，一章一文件，标题取首个 h1；跳过 `linear=no` 和 nav 文档。这一步已能覆盖大多数“一章一 XHTML”的 epub。
2. **接现成合成**：`convert_epub_file()` 逐章调用 `convert_text_to_audio`，输出 `_chapters/001_....mp3`，最后按 manifest 顺序二进制合并；复用 progress/cancel，已完成的章跳过。
3. **做 m4b 导出层**：新增 `core/audio_export.py`，把已完成的章节 mp3 导出为带章节标记/元数据/封面的 m4b（第 11 节）。它不依赖 epub，可以提前单独实现，先让 txt→m4b 跑通。
4. **接 CLI/GUI**：按后缀分派；GUI 改为读路径、显示元数据，不再对 epub 调 `read_file`；输出格式选择 MP3/M4B/两者。
5. **先写测试**：标准库生成 EPUB2 NCX + EPUB3 nav 两份夹具，mock `synthesize_chunk`；m4b 的元数据构造用纯函数测试，导出用 ffmpeg 静音样本做集成测试（无 ffmpeg 则 skip）。
6. **再加 TOC**：标题、层级、锚点切分、heading 降级。这是体验提升，不是 MVP 的硬前提。
7. **最后加细节**：脚注/ruby/图片/页码、二级切分、manifest 精确失效、DRM/fixed-layout 检测、语言自动选音色、QuickTime 章节轨道兼容。

## 10. 对照 ebook2audiobook 的取舍

参考项目：<https://github.com/DrewThomasson/ebook2audiobook>（其 EPUB 处理逻辑在 `lib/core.py`）。它的做法：

- **输入规范化**：先用 Calibre `ebook-convert` 把所有输入统一转成 EPUB，参数包括 `--flow-size=0`、`--chapter-mark=pagebreak`、`--page-breaks-before h1/h2/h3/h4/h5`、`--smarten-punctuation`。支持 pdf/docx/mobi 等。
- **明确的章节观**：代码注释直言“EPUB 没有任何章节标准”，因此内部单位叫 **block**，不叫 chapter。`get_blocks()` 用 EbookLib 读 `toc`，但实际只按 **spine 顺序**取 `ITEM_DOCUMENT`，每个文档一个 block；TOC 不参与切分。`filter_blocks()` 用 BeautifulSoup 按 `epub:type` 排除 frontmatter/backmatter/toc/titlepage/index，并把 h1–h4、`div`、`br`、表格展开成文本/停顿。
- **句子切块**：`get_sentences()` 按语言的 `max_chars/2`，优先在标点或它自己的 SML 停顿标记处切，硬切兜底，并把过短句子并入相邻句；中日韩泰等语言用 jieba/nagisa/soynlp/pythainlp 分词。
- **音频粒度**：每句一个音频文件 → `combine_audio_sentences()` 合成每个 block 一个 → `combine_audio_chapters()` 合成最终文件；拼接用 FFmpeg concat demuxer；`_generate_ffmpeg_metadata()` 写 `FFMETADATA1` 章节标记；`_export_audio()` 支持响度归一化、封面、按 `_partNN` 分片。
- **断点续传**：用 SQLite/JSON 保存 block 与 sentence 级进度，`block_hash()` 检测内容变化，`realign_blocks()`/`remap_resume()` 在重新解析后对齐旧进度；已存在的章节音频直接跳过。
- **限制**：只支持无 DRM 的书；README 明确说 epub 结构不可靠，建议用户先手工删掉不想朗读的内容。

对照结论：

| 维度 | ebook2audiobook | 本方案 | 结论 |
|---|---|---|---|
| 章节模型 | spine 文档 = block，不做 TOC 细切 | spine + TOC/heading 细切 | 保留细切，但把 block 作为兜底单位；文档与 UI 用“段落/块”描述，避免承诺“章节” |
| 输入规范化 | Calibre 统一转 EPUB | 直接解析 EPUB | 不默认引入 Calibre；预留可选 `--normalize` 处理畸形书和未来格式 |
| 清洗 | BS4 + `epub:type` 排除 + 表格压平 + 标点/数字归一 + OCR 兜底 | 已有结构过滤，缺表格/标点/OCR | 补上表格、标点归一；OCR 作为可选，不默认加依赖 |
| TTS 切块 | 每句一文件，语言分词，合并短句 | 900 字块，标点切分 | 保留；增加“尾块/过短块并入前块”的阈值，减少碎片请求 |
| 合并 | FFmpeg concat + 章节元数据 + 响度/封面/分片 | 二进制拼接 | 默认仍用二进制拼接（零依赖，edge-tts 同参数可拼）；增加可选 FFmpeg 导出层 |
| 续传 | SQLite/JSON 的 block+sentence 状态 + hash 对齐 | 文件级块缓存 + manifest | 够用；manifest 增加章节级 `block_hash` 与状态，实现按章跳过 |
| 输出粒度 | 默认全书一个文件（可 split） | 每章一个 + 全书合并 | 保留，导航更好 |
| DRM | 拒绝 | 拒绝 | 一致 |

据此对本方案的具体改进：

1. **采用 block 术语**：把“spine 文档”作为最小可信单位；TOC/heading 只是可选的细切增强。无 TOC 时 block 即一章。这与 ebook2audiobook 的保守模型一致，也避免在结构混乱的书上切出错误章节。
2. **不强制 Calibre**：直接用 `zipfile` 解析原始 EPUB，保留原书 TOC 与结构；只在明确需要（畸形 OPF、未来支持 mobi/docx/pdf）时提供可选规范化步骤。Calibre 的 `--chapter-mark=pagebreak` 本身也只是按分页符标记，不是语义章节检测，不能替代 TOC。
3. **补齐清洗能力**：表格压平、弯引号/破折号/省略号归一、日期/数字/罗马数字读法（后续）、OCR 兜底（可选）。OCR 依赖重，只对“正文为空且存在图片”的 block 提示或按开关启用。
4. **增加短块合并**：切分后若最后一块或某块非常短（例如 < 100 字），尝试并入相邻块；避免大量碎片请求和章节末尾突兀停顿。
5. **可选 FFmpeg 导出层**：保持默认零依赖的二进制拼接；新增 `--ffmpeg`（或 extra 依赖）时才做 concat demuxer、`FFMETADATA1` 章节标记、响度归一、封面、按大小/时长分片。
6. **manifest 状态机**：每章记录 `block_hash`、`text_sha1`、音色指纹、输出路径与状态；重跑时按章跳过或重建，等价于其 `block_hash`/`realign` 的简化版。
7. **MVP 顺序微调**：第一步就是“spine block-only”，这与 ebook2audiobook 的可用性对齐；TOC 细切作为第二步增强，而不是 MVP 的硬前提。

不需要照搬的部分：每句一个音频文件和语言分词器。我们的 900 字块已经远小于 Edge TTS 单次请求上限，且 Edge TTS 自己会按句/字节切分；引入 jieba 等分词器只会增加依赖，不会改善中文停顿。

## 11. 输出格式：m4b

m4b 是 MPEG-4 容器 + AAC 音频 + 章节标记 + 元数据/封面，面向 Apple Books、iOS/Android 有声书播放器。现有 mp3 路径保持不变；m4b 作为**可选导出层**，依赖系统 `ffmpeg`/`ffprobe`。

> 状态：`core/audio_export.py` 与 txt / epub 多章节导出均已实现。

### 11.1 产物与默认值

- **txt**：默认输出 m4b；`--format {mp3,m4b,both}` 可选。txt 的 m4b 只有一章（标题取文件名）或不分章。
- **epub**：默认 `m4b`：`<stem>_chapters/NNN_title.mp3` + `<stem>.m4b`；`-f both` 额外生成合并 mp3，`-f mp3` 只出 mp3。
- 章节 mp3 是缓存/中间产物，导出后默认保留；`--no-chapters` 可删除。
- 输出规则：`<stem>.m4b` 与 `-o` 主名同目录；章节文件放 `<stem>_chapters/`。

### 11.2 导出管线

1. 所有章节 mp3 已完成（逐块缓存保证可续传）。
2. 用 `ffprobe` 读每章时长，累计得到章节起止毫秒（`START`/`END`）。
3. 生成 FFconcat 列表和 FFMETADATA1 元数据文件。
4. 一条 ffmpeg 命令完成 concat → AAC 编码 → m4b，写入元数据、章节和封面。
5. 可选二次 remux 修正章节边界（见 11.3）。

```bash
# 无封面：0=concat 列表，1=元数据
ffmpeg -y -f concat -safe 0 -i chapters.txt \
  -i metadata.txt \
  -map 0:a -map_metadata 1 \
  -c:a aac -b:a 64k -ac 1 -ar 44100 \
  -movflags +faststart -f ipod book.m4b

# 有封面：0=concat 列表，1=元数据，2=封面图
ffmpeg -y -f concat -safe 0 -i chapters.txt \
  -i metadata.txt -i cover.jpg \
  -map 0:a -map 2:v -map_metadata 1 \
  -c:a aac -b:a 64k -ac 1 -ar 44100 \
  -c:v mjpeg -disposition:v attached_pic \
  -movflags +faststart -f ipod book.m4b
```

- `-f ipod` muxer 支持 `.m4b` 扩展名、默认音频编码 AAC。本机 ffmpeg 7.1.1 的 `ipod`/mov muxer 默认写 Nero `chpl` 章节原子（`-movflags +disable_chpl` 可关闭），因此章节标记不需要额外工具。
- 章节标记只写进最终 m4b，不要给每章 mp3 写章节。
- 元数据映射：`title` = 书名/文件名，`artist`/`album_artist` = 作者，`album` = 书名，`genre=Audiobook`，`comment` 可选。值里不能有换行，反斜杠按 FFMETADATA1 规则转义，控制字符剔除。
- 封面：取 EPUB 的 cover item / `guide` / `meta name="cover"`，以 `attached_pic` 写入；没有封面就跳过，不生成占位图。
- 编码：使用 ffmpeg 自带的 native `aac`（LGPL 构建可用），默认 64kbps 单声道 44.1kHz。源是 edge-tts 的 24kHz/48kbps mp3，重编码损失可忽略；`--m4b-bitrate` 可调。
- 章节时间：`TIMEBASE=1/1000`，START/END 为毫秒且单调递增；空章节、极短章节合并或过滤。
- concat 列表使用绝对路径，并按 FFconcat 规则转义单引号；路径含空格/中文没问题。

> 本机实测（ffmpeg 7.1.1）：用 `chapter.mp3` + 上述元数据单遍导出 `.m4b`，`ffprobe -show_chapters` 能读出两章（0–60s、60–120s，标题正确），`format_name` 含 mp4/m4a，音频为 64kbps；`format_tags` 中 title/artist/album/genre 正确。ffmpeg 写入的 `major_brand` 是 `M4A `，扩展名 `.m4b` 不影响 Apple Books/VLC 等按内容识别。

### 11.3 章节边界修正（推荐两遍）

单遍编码时，AAC 的 encoder priming/padding 会让总时长与章节 END 有几毫秒到几十毫秒偏差。稳妥做法：

1. 第一遍不带 chapters 编码到临时文件；
2. `ffprobe` 读实际 duration；
3. 修正最后一章 `END=duration`（必要时按比例缩放全部章节），用 `-i temp -i metadata -map_metadata 1 -c copy -f ipod` 二次 remux 到 `<output>.part.m4b`；
4. 原子替换为最终文件。

章节不多、对几十毫秒不敏感时可以只做单遍。两遍的额外成本只是一次快速 remux，建议默认开启。

### 11.4 与 core 的接口

新增 `src/ebook_to_audio/core/audio_export.py`，只依赖标准库和外部 ffmpeg，不碰 TTS：

```python
@dataclass(frozen=True)
class ChapterAudio:
    index: int
    title: str
    path: str
    duration_ms: int


@dataclass(frozen=True)
class AudiobookMeta:
    title: str
    author: str | None = None
    album: str | None = None
    cover_path: str | None = None


def export_m4b(chapters: list[ChapterAudio], output_file: str,
               meta: AudiobookMeta, *, bitrate: str = "64k",
               progress: ProgressCallback | None = None,
               cancel_check: CancelCheck | None = None) -> str: ...
```

- 进度：解析 ffmpeg `-progress pipe:1` 的 `out_time_ms`，按总时长换算；二次 remux 视为最后 5%。
- 取消：`Popen` 轮询 `cancel_check`，先 `terminate()`，3 秒后 `kill()`；清理 `.part.m4b` 与临时 concat/metadata 文件，章节 mp3 缓存保留。
- 原子性：写 `<output>.part.m4b`（显式 `-f ipod`，不依赖扩展名），成功后 `os.replace`；失败不覆盖旧文件。
- manifest 记录导出参数与 m4b 的 sha1/大小；源文件、章节、音色或导出参数未变时跳过导出。
- 检测 `ffmpeg`/`ffprobe`：`shutil.which` 缺失时抛 `ExportError`，错误信息给出 `brew install ffmpeg` / `apt install ffmpeg` 提示。

### 11.5 CLI / GUI

- CLI：`--format {mp3,m4b,both}`、`--m4b-bitrate`、`--cover/--no-cover`、`--no-chapters`。
- GUI：输出格式下拉（MP3 / M4B / 两者）；开始前检查 ffmpeg，缺失时禁用 M4B 并说明原因；完成提示里显示 m4b 路径。

### 11.6 测试

- 纯函数：章节时长累计、FFMETADATA1 文本、标题/文件名转义、空章节过滤。
- 集成（有 ffmpeg 才跑，否则 skip）：用 ffmpeg 生成两段静音 mp3 → `export_m4b` → `ffprobe -show_chapters` 校验章节数量/标题/起止时间；检查 `format_name` 为 mp4、音频 codec 为 aac；用 lavfi 生成的 jpg 验证封面 `attached_pic`。
- 回归：`--format mp3` 的行为与现有版本完全一致。
- 无网络：所有 m4b 测试都用本地生成音频，不调用 Edge TTS。

### 11.7 风险与取舍

- **外部依赖**：`ffmpeg`/`ffprobe` 必须在 PATH；不打包进项目，错误信息给安装提示。
- **章节轨道兼容性**：ffmpeg 默认写 Nero `chpl`，Apple Books、VLC、foobar2000 可读；老版 iTunes/少数播放器只认 QuickTime 章节轨道。若必须兼容，后续可选接 `mp4chaps`（mp4v2）或 AtomicParsley，非 MVP。
- **响度归一**：`loudnorm` 作为可选开关，默认关闭，避免改变 TTS 原始音色；需要时建议两遍 `loudnorm`。
- **极长书**：一次 concat + 重编码耗时与内存都可控（流式），但失败要整体重来；章节 mp3 缓存保证不必重新 TTS。若以后要中断编码，可按章编码 AAC 再 concat，复杂度更高。
- **封面必须是图片**：视频轨道需显式 `-disposition:v attached_pic`，否则播放器会当视频轨。
- **不能直接把 mp3 塞进 m4b**：mp4 容器理论上可装 mp3，但 .m4b 播放器按 AAC 处理，必须重编码。
- **文件大小**：64kbps 约 28.8MB/小时，20 小时书约 576MB。

## 参考与不确定

- 规范：EPUB 3.3 §4.3.2（mimetype）、§5.7.1–5.7.2（spine/linear/nav）、§7（nav/toc）；OPF 2.0.1 §2.4/§2.6（spine/NCX/guide）。<https://www.w3.org/TR/epub-33/>、<https://idpf.org/epub/20/spec/OPF_2.0.1_draft.htm>、<https://www.w3.org/TR/epub-ssv-11/>。
- 库信息以 PyPI 为准，落地前复核版本与维护状态（EbookLib、beautifulsoup4、lxml、mutagen）。
- **未验证**：调研环境未能联网实测 edge-tts 合成（请求被 403 拦截）；ID3 结论来自对仓库现有 mp3 的二进制检查，edge-tts 服务端输出格式未来可能变化；edge-tts 对换行是否产生额外停顿没有公开保证，设计上按“标点决定停顿”处理；EbookLib 对破损 epub 的容错未实测。
