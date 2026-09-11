"""Score converted standard text and pick the better version per file.

Layout-preserving converters shred some standards into table fragments, which
shows up as: few clause markers, a high share of table rows, and clause text
that does not read as sentences. Reading-order extraction from the same PDF
usually produces a better structure.

For every standard this script scores two candidates:

* ``01-正文Markdown-清洗版/``  the text currently used for upload
* a fresh reading-order extraction of the source PDF

When the reading-order version is clearly better it is written to
``01-正文Markdown-重转/`` so that ``clean_markdown.py`` picks it up.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from convert_pdf_reading_order import convert  # noqa: E402


# Clause numbers may sit at the start of a plain line or inside a Markdown
# heading, so both shapes must count.
CLAUSE_MARKER = re.compile(
    r"^\s{0,4}(?:#{1,6}\s*)?\d{1,2}(?:\.\d{1,2}){0,3}\s*\S",
    re.MULTILINE,
)
LIST_MARKER = re.compile(r"^\s{0,4}(?:[a-z]\)|\d{1,2}\))\s*\S", re.MULTILINE)
TABLE_ROW = re.compile(r"^\s*\|")
TOC_DOT = re.compile(r"\.{6,}")


def score(text: str) -> dict:
    lines = [line for line in text.splitlines() if line.strip()]
    total = max(len(lines), 1)
    table_rows = sum(1 for line in lines if TABLE_ROW.match(line))
    return {
        "chars": len(text),
        "lines": len(lines),
        "clause_markers": len(CLAUSE_MARKER.findall(text)),
        "list_markers": len(LIST_MARKER.findall(text)),
        "table_rows": table_rows,
        "table_ratio": round(table_rows / total, 3),
        "toc_dots": len(TOC_DOT.findall(text)),
    }


def is_better(current: dict, candidate: dict) -> tuple[bool, str]:
    if candidate["chars"] < current["chars"] * 0.7:
        return False, "重转版明显更短，疑似丢内容"
    if candidate["clause_markers"] > current["clause_markers"] * 1.2 and candidate["clause_markers"] >= 3:
        return True, "条款标记明显增多"
    if current["table_ratio"] > 0.2 and candidate["table_ratio"] < current["table_ratio"] * 0.5:
        return True, "表格碎片占比显著下降"
    if current["clause_markers"] < 3 and candidate["clause_markers"] >= 3:
        return True, "当前版本几乎没有可识别条款"
    if candidate["list_markers"] > current["list_markers"] * 1.5 and candidate["list_markers"] >= 6:
        return True, "列表项明显更完整"
    return False, "现有版本更优或差别不大"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--pdf-root", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="把更优的重转版写入 01-正文Markdown-重转/")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    import_root = args.root / "ragflow-import"
    cleaned_dir = import_root / "01-正文Markdown-清洗版"
    reconverted_dir = import_root / "01-正文Markdown-重转"
    meta = {
        row["document_id"]: row
        for row in (
            json.loads(line)
            for line in (import_root / "02-元数据卡片.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    }

    rows = []
    for card_path in sorted((import_root / "02-元数据卡片").glob("*.json")):
        card = json.loads(card_path.read_text(encoding="utf-8"))
        document_id = card["document_id"]
        current_path = cleaned_dir / Path(card["source_markdown"]).name
        if not current_path.is_file():
            rows.append({"document_id": document_id, "status": "缺少清洗版"})
            continue
        current_text = current_path.read_text(encoding="utf-8")
        current = score(current_text)

        pdf_path = args.pdf_root / str(card["source_pdf"]).replace("/", "\\")
        if not pdf_path.is_file():
            rows.append({"document_id": document_id, "status": "找不到源 PDF", "current": current})
            continue
        try:
            candidate_text = convert(pdf_path, card.get("title", ""), card.get("standard_id", ""))
        except Exception as exc:  # noqa: BLE001
            rows.append({"document_id": document_id, "status": f"重转失败：{exc}", "current": current})
            continue
        candidate = score(candidate_text)
        better, reason = is_better(current, candidate)
        if better and args.apply:
            reconverted_dir.mkdir(parents=True, exist_ok=True)
            (reconverted_dir / current_path.name).write_text(candidate_text, encoding="utf-8")
        rows.append(
            {
                "document_id": document_id,
                "standard_id": card.get("standard_id"),
                "status": "改用重转版" if better else "保留现有版本",
                "reason": reason,
                "current": current,
                "candidate": candidate,
            }
        )

    for row in rows:
        if "current" not in row:
            print(f"{row['document_id']:<24} {row['status']}")
            continue
        cur, cand = row["current"], row.get("candidate", {})
        print(
            "{:<24} {:<8} 现有: 条款{:<3} 列表{:<3} 表格{:<5} | 重转: 条款{:<3} 列表{:<3} 表格{:<5} | {}".format(
                row["document_id"],
                row["status"],
                cur["clause_markers"],
                cur["list_markers"],
                cur["table_ratio"],
                cand.get("clause_markers", "-"),
                cand.get("list_markers", "-"),
                cand.get("table_ratio", "-"),
                row.get("reason", ""),
            )
        )

    summary = {
        "files": len(rows),
        "switch_to_reconverted": [r["document_id"] for r in rows if r["status"] == "改用重转版"],
        "keep_current": [r["document_id"] for r in rows if r["status"] == "保留现有版本"],
        "problems": [r for r in rows if r["status"] not in ("改用重转版", "保留现有版本")],
    }
    if args.report:
        args.report.write_text(
            json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(summary, ensure_ascii=False)[:600])
    return 0


if __name__ == "__main__":
    sys.exit(main())
