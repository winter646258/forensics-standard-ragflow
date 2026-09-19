# -*- coding: utf-8 -*-
"""查询应用端到端验收：走完整 HTTP 链路（8899 → RAGFlow 8081）。

用法：
    python accept_query.py                       # 写到同目录 logs/验收记录.md
    python accept_query.py --questions my.json   # 自定义用例
    python accept_query.py --with-clause-text    # 保留条款原文（**仅供本地**，不要提交）

报告默认脱敏：本仓库的发布范围不含标准正文，所以命中的条款原文与含条款原文的
回答正文都会被移除，只保留标准编号、条款号、相似度与判定结果——
能证明「命中了正确标准与正确条款」的正是编号与条款号本身。
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

DEFAULT_BASE = "http://127.0.0.1:8899"

DEFAULT_QUESTIONS = [
    "电子数据搜索检验记录表应该怎么填？",
    "录音资料真实性鉴定有哪些规范要求？",
    "手机电子数据提取的操作流程是什么？",
    "数据库数据真实性鉴定适用哪个标准？",
    "破坏性程序检验的操作要点",
    "今天北京天气怎么样？",          # 无关问题，测是否会乱编
]

NOTE_LINES = [
    "> **发布版说明**：本报告按仓库「内容边界」做了脱敏——命中的**条款原文**与含条款原文的",
    "> **回答正文**已移除，仅保留标准编号、条款号、相似度与判定结果。",
    "> 能证明「命中了正确标准与正确条款」的正是编号与条款号本身，证据效力不变。",
    "> 需要含原文的完整版本时加 `--with-clause-text`，并**只留本地**，不要提交。",
]

STD_RE = re.compile(r"(?:GB/T|GA/T|GA|SF/[ZT]|SF)\s*[A-Z]{0,4}\s*\d{3,7}\s*[-—–]{1,2}\s*\d{4}")
CLAUSE_REF_RE = re.compile(r"第\s*[0-9A-Za-z．.]+\s*条")
BLOCK_HEAD_RE = re.compile(r"^\s*(?:#+\s*)?(\d+(?:\.\d+){1,4})\b")


def clause_number(text):
    """从切片或整段文字里挑出条款号：优先 `第 x 条`，否则取行首的数字编号。"""
    if not text:
        return None
    m = CLAUSE_REF_RE.search(text)
    if m:
        return m.group(0).strip()
    first = text.splitlines()[0] if text.splitlines() else ""
    m = BLOCK_HEAD_RE.match(first)
    if not m:
        return None
    token = m.group(1)
    if re.fullmatch(r"(?:19|20)\d{2}", token):          # 四位年份，不是条款号
        return None
    return token


def is_citation_only(line):
    """整行除标准编号与条款号外没有别的文字，可安全公开。"""
    t = STD_RE.sub("", line or "")
    t = CLAUSE_REF_RE.sub("", t)
    t = re.sub(r"[\s、，,。；;：:和及至\-—–（）()]+", "", t)
    return t == ""


def redact_answer(answer):
    """去掉含条款原文的正文，留下可公开的条款定位与拒答说明。

    返回 (保留的行, 是否删过正文)。
    """
    kept, dropped = [], False
    for raw in (answer or "").splitlines():
        line = raw.rstrip()
        text = line.strip()
        if not text or text in ("---", "依据"):
            continue
        if line[:1].isspace():                       # 正文列表项的续行
            dropped = True
            continue
        if text.startswith("现有已入库") or text.startswith("候选标准"):
            kept.append(text)                        # 拒答说明与本项目自研卡片字段
            continue
        if text.startswith("- ") and "｜" in text:    # 依据清单：编号 + 条款号 + 文件名
            kept.append(text)
            continue
        if text.startswith("依据") and is_citation_only(text[2:]):
            kept.append(text)
            continue
        dropped = True
    return kept, dropped


def post(base, path, payload, timeout):
    req = urllib.request.Request(
        base + path, data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json"})
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace")), time.time() - started
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        return {"ok": False, "error": "HTTP %s %s" % (exc.code, detail)}, time.time() - started
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": repr(exc)}, time.time() - started


def main():
    ap = argparse.ArgumentParser(description="查询应用端到端验收")
    ap.add_argument("--base", default=DEFAULT_BASE, help="查询应用地址")
    ap.add_argument("--out", default=None, help="报告路径；默认写到同目录 logs/验收记录.md")
    ap.add_argument("--questions", default=None, help="用例 JSON（字符串数组）")
    ap.add_argument("--timeout", type=int, default=300, help="单次请求超时秒数")
    ap.add_argument("--with-clause-text", action="store_true",
                    help="保留条款原文（**仅供本地**，不要提交到仓库）")
    args = ap.parse_args()

    questions = DEFAULT_QUESTIONS
    if args.questions:
        with open(args.questions, encoding="utf-8") as fh:
            questions = json.load(fh)

    out_path = args.out or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        "logs", "验收记录.md")

    lines = ["# 查询应用端到端验收", ""]
    lines.append("被测对象：`ragflow-query` 本地查询应用（%s → RAGFlow 8081）" % args.base)
    lines.append("验收时间：%s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    lines.append("")
    if not args.with_clause_text:
        lines.extend(NOTE_LINES)
        lines.append("")

    stats = {"ref_ok": 0, "chat_ok": 0, "n": 0, "ref_t": 0.0, "chat_t": 0.0}

    for index, question in enumerate(questions, 1):
        stats["n"] += 1
        lines.append("## 用例 %d：%s" % (index, question))
        lines.append("")

        refs_res, ref_elapsed = post(args.base, "/api/retrieve",
                                     {"question": question}, args.timeout)
        chat_res, chat_elapsed = post(args.base, "/api/chat",
                                      {"question": question}, args.timeout)

        if refs_res.get("ok"):
            stats["ref_ok"] += 1
            stats["ref_t"] += ref_elapsed
            refs = refs_res.get("refs") or []
            lines.append("**引用**：命中 %d 个来源，耗时 %.1fs（接口内计 %.1fs）"
                         % (len(refs), ref_elapsed, refs_res.get("elapsed", -1)))
            lines.append("")
            if refs:
                lines.append("| 标准编号 | 标题 | 来源 | 相似度 |")
                lines.append("|---|---|---|---|")
                for ref in refs[:6]:
                    lines.append("| `%s` | %s | %s | %.4f |" % (
                        ref.get("code", ""), (ref.get("title") or "—")[:34],
                        ref.get("source", ""), ref.get("best", 0)))
                lines.append("")
                top = refs[0]
                snippets = top.get("snippets") or [{}]
                if args.with_clause_text:
                    lines.append("首要命中片段（截 260 字）：")
                    lines.append("")
                    lines.append("> " + (snippets[0].get("text", "") or "")[:260]
                                 .replace("\n", "\n> "))
                    lines.append("")
                else:
                    tokens = [clause_number(x.get("text") or "") for x in snippets]
                    tokens = list(dict.fromkeys(t for t in tokens if t))
                    lines.append("**首要命中片段的条款定位**（原文按仓库「内容边界」移除）：")
                    lines.append("")
                    lines.append("> " + ("、".join("`%s`" % t for t in tokens)
                                          if tokens else "（未识别条款号）"))
                    lines.append("")
        else:
            lines.append("**引用**：失败 —— %s" % refs_res.get("error"))
            lines.append("")

        if chat_res.get("ok"):
            stats["chat_ok"] += 1
            stats["chat_t"] += chat_elapsed
            answer = chat_res.get("answer", "")
            lines.append("**回答**：%d 字，耗时 %.1fs（接口内计 %.1fs）"
                         % (len(answer), chat_elapsed, chat_res.get("elapsed", -1)))
            lines.append("")
            if args.with_clause_text:
                lines.append("> " + answer.replace("\n", "\n> "))
            else:
                kept, dropped = redact_answer(answer)
                if dropped:
                    lines.append("> （回答正文含条款原文，按仓库「内容边界」移除；条款定位如下）")
                    if kept:
                        lines.append("> ")
                for kept_line in kept:
                    lines.append("> " + kept_line)
                if not kept and not dropped:
                    lines.append("> （回答为空）")
            lines.append("")
        else:
            lines.append("**回答**：失败 —— %s" % chat_res.get("error"))
            lines.append("")

        lines.append("---")
        lines.append("")

    lines.append("## 汇总")
    lines.append("")
    lines.append("- 用例数：%d" % stats["n"])
    lines.append("- 引用成功：%d/%d（平均 %.1fs）"
                 % (stats["ref_ok"], stats["n"], stats["ref_t"] / max(1, stats["n"])))
    lines.append("- 回答成功：%d/%d（平均 %.1fs）"
                 % (stats["chat_ok"], stats["n"], stats["chat_t"] / max(1, stats["n"])))
    lines.append("")

    directory = os.path.dirname(out_path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")

    print("引用成功 %d/%d  平均 %.1fs"
          % (stats["ref_ok"], stats["n"], stats["ref_t"] / max(1, stats["n"])))
    print("回答成功 %d/%d  平均 %.1fs"
          % (stats["chat_ok"], stats["n"], stats["chat_t"] / max(1, stats["n"])))
    print("报告 -> %s" % out_path)
    return 0 if stats["ref_ok"] == stats["n"] and stats["chat_ok"] == stats["n"] else 1


if __name__ == "__main__":
    sys.exit(main())
