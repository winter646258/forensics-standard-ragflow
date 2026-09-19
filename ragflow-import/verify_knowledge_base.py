# -*- coding: utf-8 -*-
"""RAGFlow 知识库验收：库存核对 + 检索命中 + 端到端问答引用合规。

一次运行产出三节报告（对应 `09-检索验收记录.md` 的验收口径）：

1. **库存核对**：正文库文档数 / 切片数 / 解析运行状态，以及 ES 索引的 docs 与健康度；
2. **检索探针**：对每个问题调 `/api/v1/retrieval`，报告耗时、命中数与顶级命中的文档名、相似度；
3. **端到端问答**：用 `chat_id` 调 `/api/v1/chat/completions`（**不是** OpenAI 风格的 `model` 字段），
   并按规则校验回答：
   - 是否含标准编号（GB/T、GA/T、SF/Z、SF/T）；
   - 是否含条款号（「第 X 条」或形如 `5.1.4.1` 的编号）；
   - 是否出现内部标记 `[ID:`；
   - `expect=refuse` 的用例应明确拒答，而不是硬推荐具体标准。

用法
----
    python verify_knowledge_base.py ^
        --dataset 8baeefaeac4611f1aa6f8359b03fb8ce ^
        --questions questions.json ^
        --out 16-验收报告.md

`--questions` 省略时使用内置默认用例。JSON 结构：

    {
      "retrieval": ["问题1", "问题2"],
      "chat": [
        {"label": "即时通讯", "q": "…", "expect": "cite"},
        {"label": "证据不足", "q": "…", "expect": "refuse"}
      ]
    }

注意
----
RAGFlow v0.27.x 的 `/api/v1/chat/completions` 从 **`chat_id`** 取助手，
传 `model` 会被忽略并回退到「无知识库的默认对话」，表现为引用为空、模型换成租户默认。
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

DEFAULT_BASE = "http://127.0.0.1:8081/api/v1"
DEFAULT_DOCKER = r"C:\Program Files\Docker\Docker\resources\bin\docker.exe"
DEFAULT_MYSQL = "docker-mysql-1"
DEFAULT_ES = "docker-es01-1"

# 标准编号：要能吃掉中文回答里的常见写法——
#   「GB/T 29361-2023」「GA/T 1069-2021」「SF/T 0157-2023」
#   「SF/Z JD0402003-2015」（编号里带 JD 字母段）
#   「SF/Z JD0401002——2015」（全角双破折号）
STD_RE = re.compile(r"(?:GB/T|GA/T|GA|SF/[ZT]|SF)\s*[A-Z]{0,4}\s*\d{3,7}\s*[-—–]{1,2}\s*\d{4}")
# 条款号：「第 5.3.1 条」「第3.1条」「第 16 条」，或裸的 5.1.4.1 式编号
CLAUSE_RE = re.compile(r"第\s*[0-9A-Za-z．.]+\s*条|\b\d+(?:\.\d+){1,3}\b")
ID_RE = re.compile(r"\[ID:\d+\]")
REFUSE_HINT = re.compile(r"无法|不能|不能确定|不予|不构成|需要?补充|请补充|无法判断|不能作|不适用|证据不足")

DEFAULT_QUESTIONS = {
    "retrieval": [
        "即时通讯记录检验中，搭建检验环境有哪些要求？",
        "数据库数据真实性鉴定应当检验哪些内容？",
    ],
    "chat": [
        {"label": "即时通讯", "q": "即时通讯记录检验中，搭建检验环境有哪些具体要求？", "expect": "cite"},
        {"label": "手机提取", "q": "手机电子数据提取时，对提取过程有哪些要求？", "expect": "cite"},
        {"label": "证据不足", "q": "只有一份来源不明的 PDF，请直接给出适用条款。", "expect": "refuse"},
    ],
}


def mysql_scalar(docker, container, sql):
    proc = subprocess.run(
        [docker, "exec", container, "sh", "-c",
         'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" --default-character-set=utf8mb4 '
         '-N -B rag_flow -e "%s"' % sql],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return (proc.stdout or "").strip()


def mysql_lines(docker, container, sql):
    return [ln for ln in mysql_scalar(docker, container, sql).splitlines() if ln.strip()]


def es_indices(docker, container):
    proc = subprocess.run(
        [docker, "exec", container, "sh", "-c",
         'curl -s -u "elastic:$ELASTIC_PASSWORD" '
         '"http://localhost:9200/_cat/indices?h=index,health,docs.count,store.size&s=index"'],
        capture_output=True, text=True,
    )
    return (proc.stdout or "").strip()


class Ragflow(object):
    def __init__(self, base, token):
        self.base = base.rstrip("/")
        self.token = token

    def api(self, path, payload, timeout=600):
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": "Bearer " + self.token,
                     "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))


def check_answer(text, expect):
    """按用例期望校验回答。

    - `expect=cite`：必须给出标准编号与条款号（可回溯的核心承诺）；
    - `expect=refuse`：必须明确表达无法作答或需要补充，**不得**只是硬推荐标准；
    - 两者都必须不出现 RAGFlow 的内部标记 `[ID:x]`。
    """
    checks = {"无 [ID: 标记": not ID_RE.search(text)}
    if expect == "refuse":
        checks["按要求拒答/澄清"] = bool(REFUSE_HINT.search(text))
    else:
        checks["含标准编号"] = bool(STD_RE.search(text))
        checks["含条款号"] = bool(CLAUSE_RE.search(text))
    return checks


# 报告默认脱敏：标准正文不在本仓库的发布范围内。条款定位（编号）是判定依据，
# 条款原文不是——所以只留编号，原文一律不进报告。
NOTE_LINES = [
    "> **发布版说明**：本报告按仓库「内容边界」做了脱敏——命中的**条款原文**与含条款原文的",
    "> **回答正文**已移除，仅保留标准编号、条款号、相似度与判定结果。",
    "> 能证明「命中了正确标准与正确条款」的正是编号与条款号本身，证据效力不变。",
    "> 需要含原文的完整版本时加 `--with-clause-text`，并**只留本地**，不要提交。",
]

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
            kept.append(text)                        # 拒答说明与本仓库自研卡片字段
            continue
        if text.startswith("- ") and "｜" in text:    # 依据清单：编号 + 条款号 + 文件名
            kept.append(text)
            continue
        if text.startswith("依据") and is_citation_only(text[2:]):
            kept.append(text)
            continue
        dropped = True
    return kept, dropped


def main():
    ap = argparse.ArgumentParser(description="RAGFlow 知识库验收（库存 + 检索 + 端到端）")
    ap.add_argument("--dataset", required=True, help="正文库 dataset_id")
    ap.add_argument("--base-url", default=DEFAULT_BASE)
    ap.add_argument("--docker", default=DEFAULT_DOCKER)
    ap.add_argument("--mysql-container", default=DEFAULT_MYSQL)
    ap.add_argument("--es-container", default=DEFAULT_ES)
    ap.add_argument("--token", default=None)
    ap.add_argument("--dialog", default=None, help="助手 chat_id；省略则从 MySQL 读第一条")
    ap.add_argument("--questions", default=None, help="用例 JSON 路径")
    ap.add_argument("--out", default=None, help="把报告写到该文件")
    ap.add_argument("--skip-chat", action="store_true", help="只跑库存与检索，不跑问答")
    ap.add_argument("--with-clause-text", action="store_true",
                    help="报告保留条款原文（仅供本地核对，不要提交到仓库）")
    args = ap.parse_args()

    out = []

    def say(line=""):
        out.append(str(line))
        print(line, flush=True)

    say("# 知识库验收报告")
    say("")
    if not args.with_clause_text:
        for note_line in NOTE_LINES:
            say(note_line)
        say("")

    token = args.token or os.environ.get("RAGFLOW_API_TOKEN")
    if not token:
        rows = mysql_scalar(args.docker, args.mysql_container,
                            "SELECT token FROM api_token LIMIT 1").splitlines()
        token = rows[0].strip() if rows else ""
    if not token:
        say("无法获取 API Token：请用 --token 或设置 RAGFLOW_API_TOKEN")
        return 2

    cases = DEFAULT_QUESTIONS
    if args.questions:
        with open(args.questions, encoding="utf-8") as fh:
            cases = json.load(fh)

    # ---- 1. 库存核对 ----
    say("## 1. 库存核对")
    say("")
    doc_stat = mysql_scalar(
        args.docker, args.mysql_container,
        "SELECT COUNT(*), IFNULL(SUM(chunk_num),0), IFNULL(SUM(token_num),0) "
        "FROM document WHERE kb_id='%s'" % args.dataset)
    say("- MySQL 正文库 文档数/切片数/token 数：`%s`" % doc_stat)
    say("- 解析运行状态分布（run=3 成功, run=4 失败）：")
    for line in mysql_lines(args.docker, args.mysql_container,
                            "SELECT run, COUNT(*) FROM document WHERE kb_id='%s' GROUP BY run"
                            % args.dataset):
        say("  - `%s`" % line)
    say("- ES 索引：")
    say("")
    say("```")
    say(es_indices(args.docker, args.es_container))
    say("```")
    say("")

    rf = Ragflow(args.base_url, token)

    # ---- 2. 检索探针 ----
    say("## 2. 检索探针")
    say("")
    for question in cases.get("retrieval", []):
        payload = {"question": question, "dataset_ids": [args.dataset], "top_k": 6}
        started = time.time()
        try:
            resp = rf.api("/retrieval", payload, timeout=180)
            data = resp.get("data") or {}
            chunks = data.get("chunks") if isinstance(data, dict) else None
            say("- **%s** → code=%s，%.1fs，命中 %d"
                % (question, resp.get("code"), time.time() - started, len(chunks or [])))
            for chunk in (chunks or [])[:3]:
                doc = (chunk.get("document_keyword") or chunk.get("docnm_kwd")
                       or chunk.get("document_name"))
                say("  - `%s` sim=%s" % (doc, chunk.get("similarity")))
                content = chunk.get("content") or ""
                if args.with_clause_text:
                    say("    > %s" % content.replace("\n", " ")[:140])
                else:
                    say("    > 条款 `%s`（原文按仓库「内容边界」移除）"
                        % (clause_number(content) or "未识别"))
        except Exception as exc:  # noqa: BLE001
            say("- **%s** → 异常 %r" % (question, exc))
    say("")

    # ---- 3. 端到端问答 ----
    if args.skip_chat:
        return _finish(out, args.out)

    say("## 3. 端到端问答")
    say("")
    dialog = args.dialog
    if not dialog:
        rows = mysql_scalar(args.docker, args.mysql_container,
                            "SELECT id FROM dialog LIMIT 1").splitlines()
        dialog = rows[0].strip() if rows else ""
    if not dialog:
        say("未取到助手 chat_id，跳过问答。")
        return _finish(out, args.out)

    passed = total = 0
    for case in cases.get("chat", []):
        question, expect = case.get("q"), case.get("expect", "cite")
        label = case.get("label") or question[:20]
        started = time.time()
        try:
            resp = rf.api("/chat/completions",
                          {"chat_id": dialog, "question": question, "stream": False},
                          timeout=600)
            answer = ((resp.get("data") or {}).get("answer")) or ""
            checks = check_answer(answer, expect)
            total += 1
            ok = all(checks.values())
            passed += 1 if ok else 0
            say("### [%s] %s" % (label, "PASS" if ok else "FAIL"))
            say("")
            say("- 问题：%s" % question)
            say("- 模型期望：`%s`；耗时 %.1fs；回答长度 %d"
                % (expect, time.time() - started, len(answer)))
            say("- 规则：" + "；".join("%s=%s" % (k, "✓" if v else "✗")
                                      for k, v in checks.items()))
            say("")
            if args.with_clause_text:
                # 完整保留回答，便于逐条核对引用是否落到真实条款（仅供本地）。
                say("> " + answer.replace("\n", "\n> "))
            else:
                kept, dropped = redact_answer(answer)
                if dropped:
                    say("> （回答正文含条款原文，按仓库「内容边界」移除；条款定位如下）")
                    if kept:
                        say("> ")
                for kept_line in kept:
                    say("> " + kept_line)
                if not kept and not dropped:
                    say("> （回答为空）")
            say("")
        except Exception as exc:  # noqa: BLE001
            say("### [%s] ERROR" % label)
            say("")
            say("- 异常：%r" % (exc,))
            say("")

    say("**问答用例通过 %d/%d。**" % (passed, total))
    return _finish(out, args.out)


def _finish(lines, out_path):
    if out_path:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        print("\n报告已写入: %s" % out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
