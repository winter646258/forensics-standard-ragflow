"""Build one Markdown card per standard for the catalog knowledge base.

The catalog knowledge base holds metadata only: standard number, title, system,
status, body availability, review state and tags. It never contains clause
text, so it can answer "which standard applies" without being able to answer
"what does clause X say".

Sources:

* ``06-标准总目录候选.jsonl``  candidate catalog (all entries unverified)
* ``02-元数据卡片.jsonl``       detailed cards for the standards whose text was obtained

Output: ``11-标准卡片索引/<standard-id>.md``
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def slug(standard_id: str) -> str:
    return re.sub(r"[^0-9A-Za-z]+", "-", standard_id).strip("-").lower()


def bullet(label: str, value) -> str:
    if isinstance(value, list):
        value = "、".join(str(item) for item in value if str(item).strip())
    text = "" if value is None else str(value).strip()
    return f"- {label}：{text}" if text else f"- {label}：未提供"


def render_card(catalog: dict, detail: dict | None) -> str:
    standard_id = catalog.get("standard_id", "")
    title = catalog.get("title", "")
    lines = [f"# {standard_id} {title}".strip(), ""]

    lines.append(bullet("标准编号", standard_id))
    lines.append(bullet("标准名称", title))
    lines.append(bullet("标准体系", catalog.get("standard_system")))
    # Detailed cards were built from the official notice plus file hashes, so
    # they outrank the unverified candidate catalog for the same standard.
    status = (detail or {}).get("status") or catalog.get("status")
    body_status = (detail or {}).get("body_status") or catalog.get("body_status")
    review_status = (detail or {}).get("review_status") or catalog.get("review_status")
    if detail and "primary_recommendation" in detail:
        can_recommend = bool(detail.get("primary_recommendation"))
    else:
        can_recommend = bool(catalog.get("primary_recommendation"))

    lines.append(bullet("标准状态", status))
    lines.append(bullet("正文状态", body_status))
    lines.append(bullet("复核状态", review_status))
    lines.append(bullet("可否作为主推荐", "可以" if can_recommend else "否（状态待核验或正文未取得）"))

    if detail:
        lines.append(bullet("适用阶段", detail.get("stage_tags")))
        lines.append(bullet("适用对象", detail.get("object_tags")))
        lines.append(bullet("技术动作", detail.get("action_tags")))
        lines.append(bullet("适用场景", detail.get("scenario_tags")))
        lines.append(bullet("适用范围摘要", detail.get("scope_summary")))
        lines.append(bullet("发布与实施", f"{detail.get('published_date', '')} 发布，{detail.get('effective_date', '')} 实施"))
        lines.append(bullet("替代关系", detail.get("supersedes")))
        lines.append(bullet("官方来源", detail.get("source_url")))
        lines.append(bullet("来源证据", detail.get("source_evidence")))
    else:
        lines.append(bullet("适用阶段", None))
        lines.append(bullet("适用对象", None))
        lines.append(bullet("技术动作", None))
        lines.append(bullet("适用场景", None))
        lines.append(bullet("适用范围摘要", "未核验，仅凭标准名称判断，不得据此给出适用结论"))
        lines.append(bullet("官方来源", "未核验"))

    lines.append(bullet("数据来源", catalog.get("source_basis")))
    lines.append("")
    lines.append(
        "> 本卡片仅为目录层元数据。状态或来源未经权威核验，或正文未取得时，"
        "不得输出任何条款号、条款原文或强制适用结论。"
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    import_root = args.root / "ragflow-import"
    catalog_rows = load_jsonl(import_root / "06-标准总目录候选.jsonl")
    detail_rows = load_jsonl(import_root / "02-元数据卡片.jsonl")
    detail_by_id = {row.get("standard_id"): row for row in detail_rows}

    target = import_root / "11-标准卡片索引"
    target.mkdir(parents=True, exist_ok=True)
    for existing in target.glob("*.md"):
        existing.unlink()

    written = []
    for row in catalog_rows:
        detail = detail_by_id.get(row.get("standard_id"))
        card = render_card(row, detail)
        name = f"{slug(row.get('standard_id', 'unknown'))}.md"
        (target / name).write_text(card, encoding="utf-8")
        written.append(
            {
                "file": name,
                "standard_id": row.get("standard_id"),
                "has_detail": detail is not None,
                "status": row.get("status"),
            }
        )

    # Detailed cards whose standard is absent from the candidate catalog still
    # deserve an entry, otherwise their metadata would be unreachable.
    catalog_ids = {row.get("standard_id") for row in catalog_rows}
    for standard_id, detail in detail_by_id.items():
        if standard_id in catalog_ids:
            continue
        pseudo = {
            "standard_id": standard_id,
            "title": detail.get("title", ""),
            "standard_system": detail.get("standard_level"),
            "status": detail.get("status"),
            "body_status": detail.get("body_status"),
            "review_status": detail.get("review_status"),
            "primary_recommendation": detail.get("primary_recommendation"),
            "source_basis": detail.get("source_evidence"),
        }
        card = render_card(pseudo, detail)
        name = f"{slug(standard_id)}.md"
        (target / name).write_text(card, encoding="utf-8")
        written.append({"file": name, "standard_id": standard_id, "has_detail": True, "status": detail.get("status")})

    summary = {
        "cards": len(written),
        "with_detail": sum(1 for row in written if row["has_detail"]),
        "catalog_only": sum(1 for row in written if not row["has_detail"]),
    }
    if args.report:
        args.report.write_text(
            json.dumps({"summary": summary, "cards": written}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
