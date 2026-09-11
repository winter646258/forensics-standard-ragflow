"""Remove page furniture from converted standard Markdown before RAGFlow import.

The cleanup is deliberately narrow and mechanical. It deletes only:

* table-of-contents lines made of dot leaders,
* standalone page numbers,
* repeated running headers that repeat the standard number.

Clause text, tables and the original files are left untouched. Originals stay in
01-正文Markdown for traceability; cleaned copies go to 01-正文Markdown-清洗版.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path


TOC_LINE = re.compile(r"^[\s\|]*[^\n]{0,80}?\.{6,}[^\n]*$")
LEADER_ONLY = re.compile(r"^[\s\.·・…\|]+$")
PAGE_NUMBER = re.compile(r"^\s*(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]{1,3}|[0-9]{1,3})\s*$")
RUNNING_HEADER = re.compile(r"^\s*#{3,6}\s*SF/[ZT]\s*JD\d{7}[—\-－]{1,2}\d{4}\s*$")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def clean_text(text: str) -> tuple[str, dict[str, int]]:
    removed = {"toc": 0, "page_number": 0, "running_header": 0, "leader_only": 0}
    kept: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            kept.append("")
            continue
        if RUNNING_HEADER.match(line):
            removed["running_header"] += 1
            continue
        if LEADER_ONLY.match(line):
            removed["leader_only"] += 1
            continue
        if TOC_LINE.match(line):
            removed["toc"] += 1
            continue
        if PAGE_NUMBER.match(line):
            removed["page_number"] += 1
            continue
        kept.append(line)

    output: list[str] = []
    blank_run = 0
    for line in kept:
        if line:
            blank_run = 0
            output.append(line)
        else:
            blank_run += 1
            if blank_run <= 1:
                output.append(line)
    return "\n".join(output).strip() + "\n", removed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    source_dir = args.root / "ragflow-import" / "01-正文Markdown"
    reconverted_dir = args.root / "ragflow-import" / "01-正文Markdown-重转"
    target_dir = args.root / "ragflow-import" / "01-正文Markdown-清洗版"
    if not source_dir.is_dir():
        print(f"找不到源目录：{source_dir}", file=sys.stderr)
        return 2
    target_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for source in sorted(source_dir.glob("*.md")):
        # Files rebuilt from the PDF in reading order are already clean; copying
        # them through keeps a single upload directory without regressing them.
        reconverted = reconverted_dir / source.name
        if reconverted.is_file():
            cleaned = reconverted.read_text(encoding="utf-8")
            target = target_dir / source.name
            target.write_text(cleaned, encoding="utf-8")
            results.append(
                {
                    "file": source.name,
                    "source": "reading-order-reconvert",
                    "chars_before": len(source.read_text(encoding="utf-8")),
                    "chars_after": len(cleaned),
                    "removed": {"toc": 0, "page_number": 0, "running_header": 0, "leader_only": 0},
                    "removed_total": 0,
                    "sha256_before": sha256(source),
                    "sha256_after": sha256(target),
                }
            )
            continue
        original = source.read_text(encoding="utf-8")
        cleaned, removed = clean_text(original)
        target = target_dir / source.name
        target.write_text(cleaned, encoding="utf-8")
        results.append(
            {
                "file": source.name,
                "source": "mechanical-clean",
                "chars_before": len(original),
                "chars_after": len(cleaned),
                "removed": removed,
                "removed_total": sum(removed.values()),
                "sha256_before": sha256(source),
                "sha256_after": sha256(target),
            }
        )

    summary = {"files": len(results), "results": results}
    if args.report:
        args.report.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for row in results:
        print(
            f"{row['file']}: -{row['removed_total']} 行 "
            f"(目录 {row['removed']['toc']}, 页眉 {row['removed']['running_header']}, "
            f"页码 {row['removed']['page_number']}), {row['chars_before']} → {row['chars_after']} 字符"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
