# -*- coding: utf-8 -*-
"""重建 RAGFlow 目录库：清空后上传最新标准卡片。

与 import_texts_to_ragflow.py 的区别
------------------------------------
正文库是「有则跳过」的**增量导入**；目录库的卡片会被反复重生成
（官方状态、替代关系、来源 URL 都会变），所以必须「**先清后传**」——
否则同名文件会被 RAGFlow 存成 `xx(1).md`，同一标准在库里出现两份。

用法
----
    python rebuild_catalog.py --dataset <目录库id> --purge
    python rebuild_catalog.py --dataset <目录库id> --purge --dry-run

前置：已用 `目录库备份-*.json` 导出过现状（脚本不回滚，回滚靠备份）。
"""

import argparse
import json
import os
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


def log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def mysql_scalar(docker, container, sql):
    p = subprocess.run(
        [docker, "exec", container, "sh", "-c",
         'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" --default-character-set=utf8mb4 '
         '-N -B rag_flow -e "%s"' % sql],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    return (p.stdout or "").strip()


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
                     **({"Content-Type": ctype} if ctype else {})})
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            return {"code": e.code, "_http": True,
                    "message": e.read().decode("utf-8", "replace")[:300]}
        except Exception as e:  # noqa: BLE001 - 网络抖动统一转 dict
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

    def delete(self, ids):
        return self.api("DELETE", "/datasets/%s/documents" % self.dataset,
                        data=json.dumps({"ids": ids}).encode(),
                        ctype="application/json", timeout=300)

    def upload(self, files):
        boundary = "----RFC" + secrets.token_hex(10)
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


def wait_batch(rf, doc_ids, poll_timeout, tick=6):
    """触发解析并轮询到终态。返回 (ok, failed_ids)。"""
    rf.parse(doc_ids)
    started = time.time()
    while time.time() - started < poll_timeout:
        time.sleep(tick)
        current = {d.get("id"): d for d in rf.docs()}
        done, failed = 0, []
        for did in doc_ids:
            doc = current.get(did) or {}
            try:
                progress = float(doc.get("progress") if doc.get("progress") not in (None, "") else 0)
            except (TypeError, ValueError):
                progress = 0.0
            run = str(doc.get("run", ""))
            if progress >= 1.0 or run == RUN_DONE:
                done += 1
            elif run == RUN_FAIL or progress < 0:
                failed.append(did)
        log("    [%3ds] 完成 %d 失败 %d / %d" % (int(time.time() - started), done, len(failed), len(doc_ids)))
        # 必须等**整批**都到终态再返回：早先「一遇失败就返回」会让同批
        # 其他仍在解析的文档统计丢失（实测把 86 份成功报成了 38 份）。
        if done + len(failed) == len(doc_ids):
            return (not failed), failed
    log("    轮询超时（%ds）" % poll_timeout)
    return False, []


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description="重建 RAGFlow 目录库（清空后上传最新卡片）")
    ap.add_argument("--dataset", required=True, help="目录库 dataset_id")
    ap.add_argument("--cards", default=os.path.join(here, "11-标准卡片索引"),
                    help="卡片目录，默认脚本同级的 11-标准卡片索引")
    ap.add_argument("--base-url", default=DEFAULT_BASE)
    ap.add_argument("--docker", default=DEFAULT_DOCKER)
    ap.add_argument("--mysql-container", default=DEFAULT_MYSQL_CONTAINER)
    ap.add_argument("--token", default=None)
    ap.add_argument("--purge", action="store_true", help="先清空库内现有文档")
    ap.add_argument("--batch", type=int, default=8, help="每批上传份数")
    ap.add_argument("--poll-timeout", type=int, default=300)
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不改动线上")
    args = ap.parse_args()

    token = args.token or os.environ.get("RAGFLOW_API_TOKEN")
    if not token:
        token = mysql_scalar(args.docker, args.mysql_container,
                             "SELECT token FROM api_token LIMIT 1").splitlines()
        token = token[0].strip() if token else ""
    if not token:
        log("无法获取 API Token：请用 --token 或设置 RAGFLOW_API_TOKEN")
        return 1

    if not os.path.isdir(args.cards):
        log("卡片目录不存在：%s" % args.cards)
        return 1
    cards = sorted(f for f in os.listdir(args.cards) if f.endswith(".md"))
    if not cards:
        log("卡片目录里没有 .md 文件：%s" % args.cards)
        return 1

    rf = Ragflow(args.base_url, args.dataset, token)
    existing = rf.docs()
    log("卡片目录：%s" % args.cards)
    log("本地卡片 %d 份，线上现有 %d 份" % (len(cards), len(existing)))

    if args.dry_run:
        log("[试跑] 将清空线上 %d 份，再上传 %d 份；未做任何改动。" % (len(existing), len(cards)))
        only_online = sorted(set(d.get("name") for d in existing) - set(cards))
        only_local = sorted(set(cards) - set(d.get("name") for d in existing))
        log("  仅线上有（会被删除）：%d 份 %s" % (len(only_online), only_online[:6]))
        log("  仅本地有（会被新增）：%d 份 %s" % (len(only_local), only_local[:6]))
        return 0

    # ---- 1. 清空 ----
    if args.purge:
        if not existing:
            log("线上已是空库，跳过清空。")
        else:
            ids = [d["id"] for d in existing]
            log("开始清空 %d 份 ..." % len(ids))
            for i in range(0, len(ids), 20):
                part = ids[i:i + 20]
                r = rf.delete(part)
                if r.get("code") != 0:
                    log("!! 删除失败：%s" % str(r)[:240])
                    return 1
                log("  已删除 %d/%d" % (min(i + 20, len(ids)), len(ids)))
            left = len(rf.docs())
            log("清空完成，线上剩余 %d 份" % left)
            if left:
                log("!! 仍有残留，停止以免产生重名副本")
                return 1

    # ---- 2. 上传 ----
    uploaded, failed_upload = [], []
    for i in range(0, len(cards), args.batch):
        chunk = cards[i:i + args.batch]
        files = []
        for name in chunk:
            with open(os.path.join(args.cards, name), "rb") as f:
                files.append((name, f.read()))
        r = rf.upload(files)
        if r.get("code") != 0:
            log("!! 上传失败（%d 份）：%s" % (len(chunk), str(r)[:240]))
            failed_upload += chunk
            continue
        data = r.get("data") or []
        ids = [d.get("id") for d in data if d.get("id")]
        log("  已上传 %d - %d（%d 份）" % (i + 1, i + len(chunk), len(ids)))
        uploaded += ids

    log("上传结束：拿到 %d 个文档 id，上传失败 %d 份" % (len(uploaded), len(failed_upload)))
    for n in failed_upload:
        log("   上传失败：%s" % n)

    # ---- 3. 解析 + 轮询 ----
    log("开始解析 %d 份 ..." % len(uploaded))
    ok_total, fail_total = 0, 0
    for i in range(0, len(uploaded), args.batch):
        part = uploaded[i:i + args.batch]
        ok, failed = wait_batch(rf, part, args.poll_timeout)
        if ok:
            ok_total += len(part)
            continue
        for did in failed:
            doc = next((d for d in rf.docs() if d.get("id") == did), {})
            log("  ! 批内失败，单独重试：%s" % (doc.get("name") or did))
            ok2, failed2 = wait_batch(rf, [did], args.poll_timeout)
            if ok2:
                ok_total += 1
            else:
                fail_total += 1
                log("  !! 重试仍失败：%s" % (doc.get("name") or did))

    # ---- 4. 汇总 ----
    final = rf.docs()
    total_chunks = sum(chunks_of(d) for d in final)
    log("=" * 60)
    log("线上最终：%d 份文档 / %d 个切片" % (len(final), total_chunks))
    log("解析成功 %d，失败 %d，上传失败 %d" % (ok_total, fail_total, len(failed_upload)))
    return 0 if (fail_total == 0 and not failed_upload) else 2


if __name__ == "__main__":
    sys.exit(main())
