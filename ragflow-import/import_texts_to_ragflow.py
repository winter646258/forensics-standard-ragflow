# -*- coding: utf-8 -*-
"""把清洗版正文批量导入 RAGFlow 知识库（幂等，可重复执行）。

用法
----
    python import_texts_to_ragflow.py ^
        --dataset 8baeefaeac4611f1aa6f8359b03fb8ce ^
        --source "01-正文Markdown-清洗版"

行为
----
1. 列出知识库现有文档，按标准编号（JDxxxxxxx）跳过已入库的，**不重复上传**；
2. 分批上传（默认 4 份/批，`--batch` 可调）；
3. 对 `chunk_number == 0` 或 `run == FAIL` 的文档触发解析，逐批轮询到终态；
4. 批内出现 FAIL 时，把该文档**单独重试一次**：
   并发解析会偶发单份失败（embedding 限流），单发提交必过，这不是文本问题。

凭据
----
API Token 获取顺序：`--token` → 环境变量 `RAGFLOW_API_TOKEN` → 从 MySQL 容器读 `api_token` 表。
默认不打印 Token 本身。

前置条件
--------
- RAGFlow 已就绪（`http://127.0.0.1:8081` 返回 200）；
- 正文库 `parser_id=laws`、`raptor=false`、`graphrag=false`
  （RAPTOR/GraphRAG 会追加 LLM 调用，压垮容器）。

已知坑
------
- 解析前若主进程已连续运行约 30 分钟以上，embedding 调用可能被对端断连
  （`RemoteDisconnected`）。此时重启容器即可恢复，不必改配置；
  大批量导入建议趁容器刚重启后做。
"""

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request

DEFAULT_BASE = "http://127.0.0.1:8081/api/v1"
DEFAULT_DOCKER = r"C:\Program Files\Docker\Docker\resources\bin\docker.exe"
DEFAULT_MYSQL_CONTAINER = "docker-mysql-1"

RUN_DONE = "3"
RUN_FAIL = "4"


def mysql_scalar(docker, container, sql):
    """在 MySQL 容器里执行一条查询，返回标量字符串。"""
    proc = subprocess.run(
        [docker, "exec", container, "sh", "-c",
         'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" --default-character-set=utf8mb4 '
         '-N -B rag_flow -e "%s"' % sql],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return (proc.stdout or "").strip()


class Ragflow(object):
    def __init__(self, base, dataset, token, timeout=300):
        self.base = base.rstrip("/")
        self.dataset = dataset
        self.token = token
        self.timeout = timeout

    def api(self, method, path, data=None, ctype=None, timeout=None):
        req = urllib.request.Request(
            self.base + path, data=data, method=method,
            headers={"Authorization": "Bearer " + self.token,
                     **({"Content-Type": ctype} if ctype else {})},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:400]
            return {"code": e.code, "message": body, "_http": True}
        except Exception as e:  # noqa: BLE001 - 网络抖动统一转成 dict 返回
            return {"code": -1, "message": repr(e)}

    def docs(self):
        out, page = [], 1
        while True:
            r = self.api("GET", "/datasets/%s/documents?page=%d&page_size=100"
                         % (self.dataset, page), timeout=60)
            data = r.get("data")
            items = (data.get("docs") if isinstance(data, dict) else data) or []
            if not items:
                break
            out.extend(items)
            if len(items) < 100:
                break
            page += 1
        return out

    def upload(self, files):
        boundary = "----RFB" + secrets.token_hex(10)
        buf = []
        for name, blob in files:
            buf.append(("--" + boundary).encode())
            buf.append(('Content-Disposition: form-data; name="file"; filename="%s"'
                        % name).encode("utf-8"))
            buf.append(b"Content-Type: text/markdown")
            buf.append(b"")
            buf.append(blob)
        buf.append(("--" + boundary + "--").encode())
        return self.api("POST", "/datasets/%s/documents" % self.dataset,
                        data=b"\r\n".join(buf) + b"\r\n",
                        ctype="multipart/form-data; boundary=" + boundary,
                        timeout=600)

    def parse(self, doc_ids):
        return self.api("POST", "/datasets/%s/documents/parse" % self.dataset,
                        data=json.dumps({"document_ids": doc_ids}).encode(),
                        ctype="application/json")


def chunks_of(doc):
    for key in ("chunk_count", "chunk_num"):
        val = doc.get(key)
        if val not in (None, ""):
            try:
                return int(val)
            except (TypeError, ValueError):
                pass
    return 0


def wait_batch(rf, doc_ids, poll_timeout, log, tick=15):
    """轮询一批文档直到全部终态。返回 (ok, failed_ids)。"""
    rf.parse(doc_ids)
    started = time.time()
    while time.time() - started < poll_timeout:
        time.sleep(tick)
        current = {d.get("id"): d for d in rf.docs()}
        done, failed, states = 0, [], []
        for did in doc_ids:
            doc = current.get(did) or {}
            try:
                progress = float(doc.get("progress") if doc.get("progress") not in (None, "") else 0)
            except (TypeError, ValueError):
                progress = 0.0
            run = str(doc.get("run", ""))
            states.append("%s p=%.2f run=%s ck=%d"
                          % ((doc.get("name") or did)[:34], progress, run, chunks_of(doc)))
            if progress >= 1.0 or run == RUN_DONE:
                done += 1
            elif run == RUN_FAIL or progress < 0:
                failed.append(did)
        log("  [%3ds] ok=%d fail=%d/%d | %s"
            % (int(time.time() - started), done, len(failed), len(doc_ids), " ; ".join(states)))
        # 必须等**整批**都到终态再返回：早先「一遇失败就返回」会让同批
        # 其他仍在解析的文档统计丢失（会把成功数报少）。
        if done + len(failed) == len(doc_ids):
            return (not failed), failed
    log("  等待超时（%ds）" % poll_timeout)
    return False, []


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description="批量导入清洗版正文到 RAGFlow 知识库")
    ap.add_argument("--dataset", required=True, help="正文库 dataset_id")
    ap.add_argument("--source", default=os.path.join(here, "01-正文Markdown-清洗版"),
                    help="清洗版正文目录（默认脚本同级的 01-正文Markdown-清洗版）")
    ap.add_argument("--base-url", default=DEFAULT_BASE)
    ap.add_argument("--docker", default=DEFAULT_DOCKER)
    ap.add_argument("--mysql-container", default=DEFAULT_MYSQL_CONTAINER)
    ap.add_argument("--token", default=None, help="不传则读环境变量或 MySQL")
    ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--poll-timeout", type=int, default=420)
    ap.add_argument("--max", type=int, default=0, help="只处理前 N 份（试跑用）")
    ap.add_argument("--dry-run", action="store_true", help="只列出待上传清单")
    ap.add_argument("--log", default=None, help="另存一份日志到此文件")
    args = ap.parse_args()

    lines = []

    def log(msg):
        lines.append(str(msg))
        print(msg, flush=True)
        if args.log:
            with open(args.log, "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines))

    token = args.token or os.environ.get("RAGFLOW_API_TOKEN")
    if not token:
        token = mysql_scalar(args.docker, args.mysql_container,
                             "SELECT token FROM api_token LIMIT 1").splitlines()
        token = token[0].strip() if token else ""
    if not token:
        log("无法获取 API Token：请用 --token 或设置 RAGFLOW_API_TOKEN")
        return 2

    rf = Ragflow(args.base_url, args.dataset, token)
    existing = rf.docs()
    log("== 知识库现有文档 %d 份 ==" % len(existing))
    have = {}
    for doc in existing:
        m = re.search(r"JD(\d{7})", doc.get("name") or "")
        if m:
            have.setdefault(m.group(1), doc)

    pending = []
    for name in sorted(os.listdir(args.source)):
        if not name.endswith(".md"):
            continue
        m = re.search(r"JD(\d{7})", name)
        if m and m.group(1) in have:
            continue
        pending.append(name)
    if args.max:
        pending = pending[:args.max]
    log("待上传: %d 份" % len(pending))
    for name in pending:
        log("  + " + name)
    if args.dry_run:
        return 0

    for i in range(0, len(pending), args.batch):
        batch = pending[i:i + args.batch]
        files = []
        for name in batch:
            with open(os.path.join(args.source, name), "rb") as fh:
                files.append((name, fh.read()))
        log("")
        log("=== 上传第 %d 批（%d 份）===" % (i // args.batch + 1, len(batch)))
        resp = rf.upload(files)
        data = resp.get("data")
        items = data if isinstance(data, list) else \
            ((data or {}).get("docs") if isinstance(data, dict) else [])
        log("  code=%s 返回 %d 条" % (resp.get("code"), len(items or [])))
        for it in (items or []):
            if isinstance(it, dict):
                log("  + %s id=%s" % (it.get("name"), it.get("id")))

    # ---- 解析（含历史失败件）----
    pending_docs = [d for d in rf.docs()
                    if chunks_of(d) == 0 or str(d.get("run", "")) == RUN_FAIL]
    log("")
    log("== 待解析（chunk=0 或 FAIL）: %d 份 ==" % len(pending_docs))
    for doc in pending_docs:
        log("  - %s chunks=%d run=%s" % (doc.get("name"), chunks_of(doc), doc.get("run")))

    for i in range(0, len(pending_docs), args.batch):
        chunk = pending_docs[i:i + args.batch]
        log("")
        log("=== 解析第 %d 批 ===" % (i // args.batch + 1))
        ok, failed = wait_batch(rf, [d["id"] for d in chunk], args.poll_timeout, log)
        for did in failed:
            doc = next((d for d in rf.docs() if d.get("id") == did), {})
            log("  ! 批内失败，单独重试: %s" % doc.get("name"))
            wait_batch(rf, [did], args.poll_timeout, log)

    # ---- 汇总 ----
    log("")
    log("=== 最终状态 ===")
    total = 0
    final = rf.docs()
    for doc in final:
        count = chunks_of(doc)
        total += count
        log("  %-58s chunks=%-5d run=%s" % ((doc.get("name") or "")[:58], count, doc.get("run")))
    log("文档数=%d  切片合计=%d" % (len(final), total))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print("FATAL: %r" % (exc,), file=sys.stderr)
        raise
