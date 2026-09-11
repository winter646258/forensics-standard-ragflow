"""Validate the local RAGFlow import inventory using only the Python standard library."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any


EXPECTED_DOCUMENTS = 15
HEX64 = set("0123456789ABCDEF")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path.name}:{line_number} 不是有效 JSON：{exc}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"{path.name}:{line_number} 不是 JSON 对象")
        rows.append(value)
    return rows


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def require_file(path: Path, label: str, failures: list[str]) -> bool:
    if not path.is_file():
        failures.append(f"{label}不存在：{path}")
        return False
    return True


def valid_hash(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value.upper()) <= HEX64


def validate(root: Path, pdf_root: Path | None) -> tuple[dict[str, Any], int]:
    import_root = root / "ragflow-import"
    failures: list[str] = []
    warnings: list[str] = []
    required = {
        "metadata": import_root / "02-元数据卡片.jsonl",
        "chunks": import_root / "03-正文切片关联.jsonl",
        "hashes": import_root / "04-哈希台账.jsonl",
        "quality": import_root / "00-转换质量抽查.json",
    }
    for label, path in required.items():
        require_file(path, label, failures)
    if failures:
        return {"failures": failures, "warnings": warnings}, 1

    metadata = load_jsonl(required["metadata"])
    chunks = load_jsonl(required["chunks"])
    hashes = load_jsonl(required["hashes"])
    quality = json.loads(required["quality"].read_text(encoding="utf-8"))

    if len(metadata) != EXPECTED_DOCUMENTS:
        failures.append(f"元数据数量为 {len(metadata)}，预期 {EXPECTED_DOCUMENTS}")
    if len({row.get("document_id") for row in metadata}) != len(metadata):
        failures.append("元数据存在重复 document_id")
    if len({row.get("chunk_id") for row in chunks}) != len(chunks):
        failures.append("切片关联存在重复 chunk_id")

    metadata_by_id = {row.get("document_id"): row for row in metadata}
    chunks_by_doc: dict[str, list[dict[str, Any]]] = {}
    for row in chunks:
        chunks_by_doc.setdefault(row.get("document_id", ""), []).append(row)

    markdown_checked = 0
    markdown_hash_mismatches = 0
    for document_id, row in metadata_by_id.items():
        source_markdown = row.get("source_markdown", "")
        markdown_path = root / source_markdown.replace("/", "\\")
        if not require_file(markdown_path, f"{document_id} Markdown", failures):
            continue
        markdown_checked += 1
        actual_hash = sha256(markdown_path)
        if actual_hash != str(row.get("sha256_markdown", "")).upper():
            markdown_hash_mismatches += 1
            failures.append(f"{document_id} 元数据中的 Markdown 哈希不匹配")

        document_chunks = chunks_by_doc.get(document_id, [])
        expected_ids = row.get("chunk_ids", [])
        actual_ids = [chunk.get("chunk_id") for chunk in document_chunks]
        if row.get("chunk_count") != len(document_chunks):
            failures.append(f"{document_id} chunk_count 与切片关联数量不一致")
        if set(expected_ids) != set(actual_ids):
            failures.append(f"{document_id} chunk_ids 与切片关联不一致")
        line_count = markdown_path.read_text(encoding="utf-8").count("\n") + 1
        for chunk in document_chunks:
            start = chunk.get("source_line_start")
            end = chunk.get("source_line_end")
            if not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start:
                failures.append(f"{chunk.get('chunk_id')} 行号范围无效")
            elif end > line_count:
                failures.append(f"{chunk.get('chunk_id')} 超出 Markdown 行数 {line_count}")
            if not chunk.get("text_chars"):
                failures.append(f"{chunk.get('chunk_id')} 缺少有效 text_chars")

    hash_by_key = {(row.get("document_id"), row.get("artifact_type")): row for row in hashes}
    for document_id, row in metadata_by_id.items():
        ledger = hash_by_key.get((document_id, "converted_markdown"))
        if not ledger:
            failures.append(f"{document_id} 缺少 converted_markdown 哈希台账")
            continue
        if str(ledger.get("sha256", "")).upper() != str(row.get("sha256_markdown", "")).upper():
            failures.append(f"{document_id} 元数据与哈希台账不一致")

    pdf_checked = 0
    pdf_missing = 0
    if pdf_root:
        for row in hashes:
            if row.get("artifact_type") != "source_pdf":
                continue
            pdf_path = pdf_root / str(row.get("path", "")).replace("/", "\\")
            if not pdf_path.is_file():
                pdf_missing += 1
                warnings.append(f"源 PDF 未在指定目录找到：{pdf_path}")
                continue
            pdf_checked += 1
            if pdf_path.stat().st_size != row.get("bytes"):
                failures.append(f"{row.get('document_id')} PDF 大小与台账不一致")
            if sha256(pdf_path) != str(row.get("sha256", "")).upper():
                failures.append(f"{row.get('document_id')} PDF 哈希与台账不一致")
    else:
        warnings.append("未指定 --pdf-root，源 PDF 哈希仅完成台账结构检查")

    if not isinstance(quality, list) or len(quality) != EXPECTED_DOCUMENTS:
        failures.append(f"转换质量报告数量异常：{len(quality) if isinstance(quality, list) else '非数组'}")
    else:
        quality_ids = {row.get("document_id") for row in quality}
        if quality_ids != set(metadata_by_id):
            failures.append("转换质量报告与元数据 document_id 集合不一致")
        failed_qa = [row.get("document_id") for row in quality if row.get("qa_status") != "通过机械检查；需抽查版式"]
        if failed_qa:
            failures.append(f"存在未通过机械质量检查的文件：{', '.join(map(str, failed_qa))}")

    summary = {
        "checked_at": date.today().isoformat(),
        "documents": len(metadata),
        "primary_recommendations": sum(1 for row in metadata if row.get("primary_recommendation")),
        "superseded_documents": sum(1 for row in metadata if row.get("status") == "已被代替"),
        "chunks": len(chunks),
        "markdown_checked": markdown_checked,
        "markdown_hash_mismatches": markdown_hash_mismatches,
        "pdf_checked": pdf_checked,
        "pdf_missing": pdf_missing,
        "failures": failures,
        "warnings": warnings,
    }
    return summary, 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--pdf-root", type=Path, help="原始 PDF 所在的 Obsidian Vault 根目录")
    parser.add_argument("--report", type=Path, help="将 JSON 报告写入指定路径")
    args = parser.parse_args()

    summary, exit_code = validate(args.root.resolve(), args.pdf_root.resolve() if args.pdf_root else None)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
