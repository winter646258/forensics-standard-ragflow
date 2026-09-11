"""Rebuild clause-structured Markdown from a text-layer standard PDF.

Layout-preserving converters read these standards as tables and shred the
clause text into per-cell fragments. Extracting the text layer in reading
order instead keeps the clause sequence intact, so this script:

1. extracts each page with pypdf,
2. drops running headers, footers and table-of-contents dot leaders,
3. re-joins the visual line breaks without gluing Latin words together,
4. re-inserts heading structure for chapter / clause / sub-clause numbers.

Requires pypdf. Run with the interpreter that has pypdf installed.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from pypdf import PdfReader


TOC_LINE = re.compile(r"^.*?\.{6,}.*$")
PAGE_NUMBER_ONLY = re.compile(r"^\s*(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩIVX]{1,4}|\d{1,3})\s*$")
STANDARD_LINE = re.compile(r"^\s*SF/[ZT]\s*JD\d{7}\s*$")
STANDARD_PARTS = re.compile(r"^\s*(?:SF/Z|JD\d{7}|[—\-－]{1,2}|\d{4}|2015-|11|-|20)\s*$")
CLAUSE_NUMBER_ONLY = re.compile(r"^\d{1,2}(?:\.\d{1,2}){1,2}$")

LATIN_TAIL = re.compile(r"[A-Za-z0-9)]$")
LATIN_HEAD = re.compile(r"^[A-Za-z0-9(]")

CHAPTER = re.compile(r"^(\d{1,2})\s+(\S.*)$")
CLAUSE = re.compile(r"^(\d{1,2}\.\d{1,2})\s*(\S.*)$")
SUB_CLAUSE = re.compile(r"^(\d{1,2}\.\d{1,2}\.\d{1,2})\s*(\S.*)$")
LIST_ITEM = re.compile(r"^([a-z]\)|\d{1,2}\)|示例[:：]|[a-z]\)\s)")


def extract_pages(pdf: Path, skip_cover: bool) -> list[str]:
    reader = PdfReader(str(pdf))
    texts = [(page.extract_text() or "") for page in reader.pages]
    return texts[1:] if skip_cover and len(texts) > 1 else texts


def strip_furniture(pages: list[str]) -> list[str]:
    cleaned: list[str] = []
    in_toc = False
    for raw in pages:
        page_lines = [line.strip() for line in raw.splitlines() if line.strip()]
        lines = []
        last_index = len(page_lines) - 1
        for index, stripped in enumerate(page_lines):
            if stripped in {"目 次", "目次"}:
                in_toc = True
                lines.append(stripped)
                continue
            if in_toc:
                if stripped.startswith("前") or stripped.startswith("言"):
                    in_toc = False
                else:
                    continue
            if TOC_LINE.match(stripped):
                continue
            # A bare number is only page furniture when it sits at the very
            # top or bottom of a page; elsewhere it is a chapter heading.
            if PAGE_NUMBER_ONLY.match(stripped) and (index < 6 or index > last_index - 3):
                continue
            if STANDARD_LINE.match(stripped) or STANDARD_PARTS.match(stripped):
                continue
            lines.append(stripped)
        cleaned.append("\n".join(lines))
    return cleaned


def join_stream(pages: list[str]) -> str:
    pieces: list[str] = []
    for page in pages:
        for line in page.splitlines():
            if not pieces:
                pieces.append(line)
                continue
            previous = pieces[-1]
            if CLAUSE_NUMBER_ONLY.match(previous) or CLAUSE_NUMBER_ONLY.match(line):
                pieces.append(" " + line)
            elif LATIN_TAIL.search(previous) and LATIN_HEAD.match(line):
                pieces.append(" " + line)
            else:
                pieces.append(line)
    return "".join(pieces)


def drop_cover_fragments(stream: str) -> str:
    """Cut the cover/TOC debris that precedes the foreword body."""
    anchor = stream.find("本技术规范按照")
    if anchor == -1:
        return stream
    foreword = stream.rfind("前言", 0, anchor)
    if foreword == -1:
        foreword = stream.rfind("前 言", 0, anchor)
    return stream[foreword:] if foreword != -1 else stream[anchor:]


def reflow(stream: str) -> str:
    """Insert line breaks before clause numbers and list markers."""
    stream = re.sub(r"\s+", " ", stream)
    # Clause numbers only start a new line when they follow text, not inside a
    # citation such as "GB/T 1.1-2009". The PDF often drops the space between
    # a clause number and its title, so normalise both at once.
    stream = re.sub(
        r"(?<=[。；;：:）)】])\s*(\d{1,2}(?:\.\d{1,2}){0,2})(?=[\u4e00-\u9fffA-Za-z])",
        lambda m: "\n" + m.group(1) + " ",
        stream,
    )
    stream = re.sub(
        r"(?<=[。；;：:）)】])\s*(\d{1,2})\s+(?=[\u4e00-\u9fff])",
        lambda m: "\n" + m.group(1) + " ",
        stream,
    )
    for pattern in [
        r"(?<=[。；;：:）)】])\s*(?=[a-z]\)\s)",
        r"(?<=[。；;：:）)】])\s*(?=\d{1,2}\)\s)",
        r"(?<=[a-z])\s+(?=\d{1,2}\)\s)",
    ]:
        stream = re.sub(pattern, "\n", stream)
    # A clause number directly glued to Chinese text starts a new clause.
    # Cross-references such as "依照 5.3规定" keep their leading space, so they
    # are not affected.
    stream = re.sub(
        r"(?<=[\u4e00-\u9fff])(\d{1,2}\.\d{1,2}(?:\.\d{1,2})?)",
        lambda m: "\n" + m.group(1),
        stream,
    )
    # Single-digit chapter numbers are only split when a known chapter title
    # follows, so numbers inside sentences and citations stay put.
    stream = re.sub(
        r"(?<=[\u4e00-\u9fff])\s?(\d{1,2})\s*(?=(?:范围|规范性引用文件|术语和定义|原则|步骤|记录|注意事项))",
        lambda m: "\n" + m.group(1) + " ",
        stream,
    )
    return stream


def to_markdown(stream: str, title: str, standard_id: str) -> str:
    out: list[str] = [f"# {title}", "", f"> {standard_id}", ""]
    for line in stream.splitlines():
        text = line.strip()
        if not text:
            continue
        match = SUB_CLAUSE.match(text)
        if match:
            out += [f"### {match.group(1)} {match.group(2)}", ""]
            continue
        match = CLAUSE.match(text)
        if match:
            out += [f"## {match.group(1)} {match.group(2)}", ""]
            continue
        match = CHAPTER.match(text)
        if match and len(match.group(2)) <= 40 and not text.startswith(("2009", "2012", "2015")):
            out += [f"## {match.group(1)} {match.group(2)}", ""]
            continue
        out += [text, ""]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip() + "\n"


def convert(pdf: Path, title: str, standard_id: str) -> str:
    pages = strip_furniture(extract_pages(pdf, skip_cover=True))
    stream = drop_cover_fragments(join_stream(pages))
    return to_markdown(reflow(stream), title, standard_id)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--title", required=True)
    parser.add_argument("--standard-id", required=True)
    parser.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args()

    if not args.pdf.is_file():
        print(f"找不到 PDF：{args.pdf}", file=sys.stderr)
        return 2
    markdown = convert(args.pdf, args.title, args.standard_id)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(markdown, encoding="utf-8")
    print(f"{args.output} ({len(markdown)} 字符)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
