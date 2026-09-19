# -*- coding: utf-8 -*-
"""电子取证标准查询 —— 本地 Web 应用后端。

只用 Python 标准库，无需 pip 安装任何依赖。

启动
----
    python app.py            # 启动服务
    python app.py --open     # 启动并自动打开浏览器

接口
----
    GET  /               查询页面
    GET  /api/status     健康检查（RAGFlow 是否可达、Token 是否就绪）
    POST /api/retrieve   {"question": "..."}  → 命中的标准条款（快，约 1s）
    POST /api/chat       {"question": "..."}  → 生成的答案（慢，约 10-60s）

设计说明
--------
助手关闭了引用面板（prompt_config.quote=false，为压制 [ID:0]），
因此 /chat/completions 返回的 reference 恒为空。
前端改为并行调用本服务的两个接口：先拿 retrieval 的命中条款做引用，
再等 chat 的答案。这样引用秒出、答案随后到，体验最好。
"""

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(ROOT, "static")
LOGDIR = os.path.join(ROOT, "logs")
CONFIG_PATH = os.path.join(ROOT, "config.json")

_log_lock = threading.Lock()


def log(msg):
    line = "[%s] %s" % (datetime.now().strftime("%H:%M:%S"), msg)
    with _log_lock:
        print(line, flush=True)
        try:
            os.makedirs(LOGDIR, exist_ok=True)
            with open(os.path.join(LOGDIR, "app.log"), "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


# --------------------------------------------------------------------------
# 标准编号解析
# --------------------------------------------------------------------------

_PREFIX_MAP = {
    "SF-Z": "SF/Z", "SF-T": "SF/T", "GA-T": "GA/T", "GB-T": "GB/T",
    "GB-Z": "GB/Z", "SN-T": "SN/T", "JR-T": "JR/T", "YD-T": "YD/T",
    "SF": "SF", "GA": "GA", "GB": "GB", "SN": "SN", "JR": "JR", "YD": "YD",
}


def parse_doc_name(name):
    """把文档名解析成 (标准编号, 标题)。

    SF-Z-JD0400001-2014-电子数据司法鉴定通用实施规范(1).md
        -> ("SF/Z JD0400001-2014", "电子数据司法鉴定通用实施规范")
    ga-t-1069-2021.md
        -> ("GA/T 1069-2021", "")
    """
    raw = re.sub(r"\.(md|markdown|pdf|docx?|txt)$", "", (name or "").strip(), flags=re.I)
    raw = re.sub(r"\(\d+\)\s*$", "", raw).strip()
    parts = [p for p in re.split(r"[-_]", raw) if p]
    if not parts:
        return raw, ""

    up = [p.upper() for p in parts]
    prefix, i = "", 0
    if len(up) >= 2 and (up[0] + "-" + up[1]) in _PREFIX_MAP:
        prefix, i = _PREFIX_MAP[up[0] + "-" + up[1]], 2
    elif up[0] in _PREFIX_MAP:
        prefix, i = _PREFIX_MAP[up[0]], 1

    num = up[i] if i < len(up) else ""
    year, title_bits = "", []
    for p in parts[i + 1:]:
        if not year and re.fullmatch(r"(19|20)\d{2}", p):
            year = p
            continue
        title_bits.append(p)
    title = "-".join(title_bits)

    code = (prefix + " " + num).strip()
    if year:
        code += "-" + year
    return (code or raw), title


def clean_text(t):
    """清掉切片里的小标记与多余空白。"""
    t = re.sub(r"\[ID:\d+\]", "", t or "")
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def clean_answer(a):
    return clean_text(a)


def guess_title(text, code):
    """目录库卡片文件名不含标准名称，从卡片正文里补一个。"""
    for line in (text or "").splitlines():
        line = line.strip()
        m = re.match(r"^[-*+\s]*标准名称[：:]\s*(.+)$", line)
        if not m:
            m = re.match(r"^#{1,3}\s+(.+)$", line)
        if m:
            t = m.group(1).strip()
            if code and t.startswith(code):
                t = t[len(code):].strip(" -—–·：:")
            if t:
                return t
    return ""


def aggregate_refs(chunks, cfg):
    """把切片按文档聚合成引用卡片，按最高相似度降序。"""
    labels = cfg.get("dataset_labels") or {}
    notes = cfg.get("dataset_notes") or {}
    groups = {}

    for c in chunks:
        did = c.get("document_id") or ""
        g = groups.get(did)
        if g is None:
            code, title = parse_doc_name(c.get("document_keyword") or "")
            ds = c.get("dataset_id") or ""
            g = groups[did] = {
                "document_id": did,
                "dataset_id": ds,
                "source": labels.get(ds, "未知库"),
                "source_note": notes.get(ds, ""),
                "code": code,
                "title": title,
                "doc_name": c.get("document_keyword") or "",
                "best": 0.0,
                "snippets": [],
            }
        sim = float(c.get("similarity") or 0)
        g["best"] = max(g["best"], sim)
        text = clean_text(c.get("content") or "")
        if text:
            g["snippets"].append({
                "text": text,
                "sim": round(sim, 4),
                "vector": round(float(c.get("vector_similarity") or 0), 4),
                "term": round(float(c.get("term_similarity") or 0), 4),
                "chunk_id": c.get("id") or "",
            })

    out = []
    for g in groups.values():
        g["snippets"].sort(key=lambda s: -s["sim"])
        g["snippets"] = g["snippets"][:3]
        g["best"] = round(g["best"], 4)
        g["hit_count"] = len(g["snippets"])
        if not g["title"] and g["snippets"]:
            g["title"] = guess_title(g["snippets"][0]["text"], g["code"])
        out.append(g)
    out.sort(key=lambda g: -g["best"])
    return out


# --------------------------------------------------------------------------
# 凭据与上游调用
# --------------------------------------------------------------------------

def resolve_token(cfg):
    """Token 三级回退：config.json → 环境变量 → MySQL api_token 表。"""
    t = (cfg.get("token") or "").strip()
    if t:
        return t, "config.json"

    env_key = cfg.get("token_env") or "RAGFLOW_API_TOKEN"
    t = (os.environ.get(env_key) or "").strip()
    if t:
        return t, "环境变量 %s" % env_key

    my = cfg.get("mysql") or {}
    docker = my.get("docker_exe") or "docker"
    cont = my.get("container") or "docker-mysql-1"
    try:
        p = subprocess.run(
            [docker, "exec", cont, "sh", "-c",
             'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" --default-character-set=utf8mb4 '
             '-N -B rag_flow -e "SELECT token FROM api_token LIMIT 1"'],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=30)
        lines = (p.stdout or "").strip().splitlines()
        if lines and lines[0].strip():
            return lines[0].strip(), "MySQL api_token"
        log("MySQL 未返回 Token，stderr=%s" % (p.stderr or "").strip()[:160])
    except Exception as e:
        log("从 MySQL 读 Token 失败：%r" % e)
    return "", None


def _rf_once(cfg, token, method, path, payload=None, timeout=120):
    """单次调用 RAGFlow API。返回 (body, elapsed, error)。"""
    url = cfg["base_url"].rstrip("/") + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Authorization": "Bearer " + (token or "")}
    if data:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)

    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read().decode("utf-8", "replace"))
        elapsed = time.time() - t0
        if isinstance(body, dict) and body.get("code") not in (0, None):
            return None, elapsed, "RAGFlow 返回 code=%s：%s" % (
                body.get("code"), str(body.get("message"))[:200])
        return body, elapsed, None
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", "replace")[:200]
        return None, time.time() - t0, "上游 HTTP %s：%s" % (e.code, msg)
    except socket.timeout:
        return None, time.time() - t0, "上游超时（%ss 未响应）" % timeout
    except urllib.error.URLError as e:
        return None, time.time() - t0, "无法连接 RAGFlow（%s），请确认容器在运行" % (e.reason,)
    except Exception as e:
        return None, time.time() - t0, "请求异常：%r" % e


# 上游偶发抖动：embedding 服务端断开复用连接，重试即可恢复，不必重启容器。
_TRANSIENT = (
    "remotedisconnected", "connection aborted", "connection reset",
    "read timed out", "embeddingerror", "embedding request failed",
    "max retries exceeded", "temporarily unavailable", "econnreset",
    "502 bad gateway", "503 service", "504 gateway",
)


def _is_transient(err):
    m = (err or "").lower()
    return any(k in m for k in _TRANSIENT)


def rf_request(cfg, token, method, path, payload=None, timeout=120,
               retries=0, tag=""):
    """带自动重试的调用，返回 (body, 累计耗时, error)。

    实测：SiliconFlow embedding 偶发 RemoteDisconnected 属抖动，
    同一问题重发即成功（容器无需重启），因此这里退避重试而不是报错。
    """
    total, attempt = 0.0, 0
    while True:
        body, el, err = _rf_once(cfg, token, method, path, payload, timeout)
        total += el
        if err is None or attempt >= retries or not _is_transient(err):
            return body, total, err
        attempt += 1
        wait = 2 * attempt
        log("%s 遇到可重试错误（第 %d 次）：%s → %.0f 秒后重试"
            % (tag or path, attempt, err[:130], wait))
        time.sleep(wait)


# --------------------------------------------------------------------------
# HTTP 处理
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "EvidenceQuery/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass  # 访问日志由我们自己控制，避免刷屏

    # -- 工具 --
    def _send(self, code, payload, ctype="application/json; charset=utf-8"):
        body = payload if isinstance(payload, bytes) else \
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except Exception:
            pass

    def _read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def _serve_file(self, path):
        try:
            with open(path, "rb") as f:
                data = f.read()
        except Exception:
            return self._send(404, {"error": "文件不存在：%s" % os.path.basename(path)})
        ext = os.path.splitext(path)[1].lower()
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".svg": "image/svg+xml",
            ".ico": "image/x-icon",
        }.get(ext, "application/octet-stream")
        self._send(200, data, ctype)

    def _question(self, req):
        return (req.get("question") or "").strip()[:2000]

    # -- 路由 --
    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            return self._serve_file(os.path.join(STATIC, "index.html"))
        if path == "/api/status":
            return self.api_status()
        if path.startswith("/static/"):
            rel = path[len("/static/"):]
            if ".." in rel or rel.startswith("/"):
                return self._send(400, {"error": "非法路径"})
            return self._serve_file(os.path.join(STATIC, rel))
        if path == "/favicon.ico":
            return self._send(204, b"")
        self._send(404, {"error": "未知路径"})

    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/api/retrieve":
            return self.api_retrieve()
        if path == "/api/chat":
            return self.api_chat()
        self._send(404, {"error": "未知路径"})

    # -- 接口 --
    def api_status(self):
        cfg = self.server.cfg
        info = {
            "app": cfg.get("app_name"),
            "token_ok": bool(self.server.token),
            "token_source": self.server.token_source,
            "base_url": cfg.get("base_url"),
            "chat_id": cfg.get("chat_id"),
            "datasets": [
                {"id": i,
                 "label": (cfg.get("dataset_labels") or {}).get(i, "未知库"),
                 "note": (cfg.get("dataset_notes") or {}).get(i, "")}
                for i in (cfg.get("dataset_ids") or [])
            ],
            "ragflow_ok": False,
        }
        body, el, err = rf_request(
            cfg, self.server.token, "GET", "/datasets?page=1&page_size=10",
            None, cfg.get("status_timeout", 10), retries=1, tag="status")
        if err:
            info["error"] = err
        else:
            info["ragflow_ok"] = True
            live = {d.get("id"): d for d in (body.get("data") or [])}
            for d in info["datasets"]:
                ld = live.get(d["id"])
                if ld:
                    d["documents"] = ld.get("document_count")
                    d["chunks"] = ld.get("chunk_count")
                else:
                    d["missing"] = True
        self._send(200, info)

    def api_retrieve(self):
        cfg = self.server.cfg
        q = self._question(self._read_json())
        if not q:
            return self._send(400, {"ok": False, "error": "问题不能为空"})

        rc = cfg.get("retrieval") or {}
        payload = {
            "question": q,
            "dataset_ids": cfg.get("dataset_ids") or [],
            "page": 1,
            "page_size": rc.get("page_size", 8),
            "similarity_threshold": rc.get("similarity_threshold", 0.1),
            "vector_similarity_weight": rc.get("vector_similarity_weight", 0.3),
            "top_k": rc.get("top_k", 1024),
        }
        body, el, err = rf_request(cfg, self.server.token, "POST", "/retrieval",
                                   payload, cfg.get("retrieval_timeout", 120),
                                   retries=2, tag="retrieve")
        if err:
            log("retrieve 失败：%s" % err)
            return self._send(200, {"ok": False, "error": err, "elapsed": round(el, 1)})

        data = body.get("data") or {}
        refs = aggregate_refs(data.get("chunks") or [], cfg)
        log("retrieve %.1fs 命中文档 %d 个 | %s" % (el, len(refs), q[:40]))
        self._send(200, {
            "ok": True,
            "elapsed": round(el, 1),
            "total": data.get("total"),
            "refs": refs,
        })

    def api_chat(self):
        cfg = self.server.cfg
        q = self._question(self._read_json())
        if not q:
            return self._send(400, {"ok": False, "error": "问题不能为空"})

        payload = {"chat_id": cfg.get("chat_id"), "question": q, "stream": False}
        body, el, err = rf_request(cfg, self.server.token, "POST", "/chat/completions",
                                   payload, cfg.get("chat_timeout", 300),
                                   retries=2, tag="chat")
        if err:
            log("chat 失败：%s" % err)
            return self._send(200, {"ok": False, "error": err, "elapsed": round(el, 1)})

        d = body.get("data") or {}
        answer = clean_answer(d.get("answer") or "")
        log("chat %.1fs 答案 %d 字 | %s" % (el, len(answer), q[:40]))
        self._send(200, {
            "ok": True,
            "elapsed": round(el, 1),
            "answer": answer,
            "has_reference": bool(d.get("reference")),
        })


# --------------------------------------------------------------------------
# 启动
# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="电子取证标准查询（本地 Web 应用）")
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--open", action="store_true", help="启动后自动打开浏览器")
    args = ap.parse_args()

    cfg = load_config()
    if args.host:
        cfg["host"] = args.host
    if args.port:
        cfg["port"] = args.port

    token, src = resolve_token(cfg)
    if token:
        log("Token 已就绪（来源：%s，长度 %d）" % (src, len(token)))
    else:
        log("!! 未取到 API Token —— 页面可打开，但查询会失败。")
        log('   请在 config.json 的 "token" 字段填入 RAGFlow 的 API Key。')

    host = cfg.get("host") or "127.0.0.1"
    start_port = int(cfg.get("port") or 8899)

    httpd = None
    for p in range(start_port, start_port + 20):
        try:
            httpd = ThreadingHTTPServer((host, p), Handler)
            port = p
            break
        except OSError:
            log("端口 %d 被占用，尝试 %d" % (p, p + 1))
    if httpd is None:
        log("!! %d~%d 全部端口被占用，无法启动" % (start_port, start_port + 19))
        sys.exit(1)

    httpd.cfg = cfg
    httpd.token = token
    httpd.token_source = src
    httpd.daemon_threads = True

    url = "http://%s:%d/" % (host, port)
    log("=" * 56)
    log("  %s 已启动" % (cfg.get("app_name") or "查询应用"))
    log("  地址：%s" % url)
    log("  停止：在本窗口按 Ctrl+C")
    log("=" * 56)

    if args.open:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log("收到停止信号，正在关闭…")
    finally:
        httpd.shutdown()
        log("已停止。")


if __name__ == "__main__":
    main()
