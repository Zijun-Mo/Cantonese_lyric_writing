"""最小依赖的本地前端 + API 运行服务（仅标准库）。

用法：
  python src/frontend/dev_server.py

访问：
  http://127.0.0.1:7860
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import threading
import traceback
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Tuple


_STATE_LOCK = threading.Lock()
_RUN_STATE: Dict[str, Any] = {
    "running": False,
    "step": "就绪",
    "cancel_event": None,
    "last_error": None,
    "logs": [],
}

_MAX_LOG_LINES = 120


class _ProgressHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        line = f"[{record.levelname}] {record.name}: {msg}"
        if "步骤" in msg:
            with _STATE_LOCK:
                _RUN_STATE["step"] = msg.strip()
        with _STATE_LOCK:
            logs = _RUN_STATE.get("logs")
            if not isinstance(logs, list):
                logs = []
                _RUN_STATE["logs"] = logs
            logs.append(line)
            if len(logs) > _MAX_LOG_LINES:
                del logs[: len(logs) - _MAX_LOG_LINES]


def _run_powershell(cmd: str) -> str:
    """运行 PowerShell 命令并返回 stdout（失败返回空）。"""
    try:
        p = subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        return (p.stdout or "").strip()
    except Exception:
        return ""


def _auto_kill_port_conflicts(host: str, port: int) -> None:
    """启动时自动清理同端口 dev_server 旧进程（避免重复监听）。

    只会 kill 命令行包含 dev_server.py 的 python 进程，避免误杀其他服务。
    """
    logger = logging.getLogger("frontend")
    try:
        net = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        ).stdout
    except Exception:
        return

    listen_pids = set()
    # 示例：TCP    127.0.0.1:7860         0.0.0.0:0              LISTENING       16152
    pat = re.compile(rf"^\s*TCP\s+{re.escape(host)}:{port}\s+.*\s+LISTENING\s+(\d+)\s*$", re.IGNORECASE)
    for line in (net or "").splitlines():
        m = pat.match(line)
        if m:
            listen_pids.add(int(m.group(1)))

    if not listen_pids:
        return

    this_pid = os.getpid()
    killed = []
    for pid in sorted(listen_pids):
        if pid == this_pid:
            continue
        cmdline = _run_powershell(
            f"(Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\").CommandLine"
        )
        if "dev_server.py" not in (cmdline or ""):
            continue
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, text=True)
            killed.append(pid)
        except Exception:
            continue

    if killed:
        logger.info(f"已清理端口 {host}:{port} 的旧 dev_server 进程：{killed}")


def _force_utf8_stdio() -> None:
    """避免 IDE 终端中文输出乱码。"""
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _repo_root() -> str:
    here = os.path.abspath(os.path.dirname(__file__))
    return os.path.abspath(os.path.join(here, "..", ".."))


def _read_json_body(handler: SimpleHTTPRequestHandler) -> Dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0") or "0")
    raw = handler.rfile.read(length) if length > 0 else b"{}"
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return {}


def _extract_bearer_token(auth_header: Optional[str]) -> Optional[str]:
    if not auth_header:
        return None
    parts = auth_header.strip().split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        token = parts[1].strip()
        return token or None
    return None


def _try_load_local_api_key() -> Optional[str]:
    """尝试从项目根目录的 APIKey.txt 读取 key（不回传给前端）。"""
    try:
        root = _repo_root()
        key_path = os.path.join(root, "APIKey.txt")
        if not os.path.exists(key_path):
            return None
        with open(key_path, "r", encoding="utf-8") as f:
            key = f.read().strip()
        return key or None
    except Exception:
        return None


def _json_response(handler: SimpleHTTPRequestHandler, status: int, data: Dict[str, Any]) -> None:
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    try:
        handler.wfile.write(body)
    except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
        # 客户端主动断开连接（例如前端 abort / 刷新），不应导致服务崩溃
        return


def _run_pipeline_from_payload(payload: Dict[str, Any], api_key: str) -> Dict[str, Any]:
    # 延迟导入，避免启动时污染路径/耗时
    repo_root = _repo_root()
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    from src.input.schema import LyricInput
    from src.pipeline import run_pipeline
    from src.generation.glm_client import GLMClient

    lyric_input = LyricInput(
        jianpu=str(payload.get("jianpu", "")).strip(),
        mandarin_seed=str(payload.get("mandarin_seed", "")).strip(),
        theme_tags=list(payload.get("theme_tags") or []),
        style_tags=list(payload.get("style_tags") or []),
    )

    enable_polish = not bool(payload.get("no_polish", False))
    num_candidates = int(payload.get("candidates", 10))
    if num_candidates <= 0:
        num_candidates = 10

    client = GLMClient(api_key=api_key)
    with _STATE_LOCK:
        cancel_event = _RUN_STATE.get("cancel_event")

    return run_pipeline(
        lyric_input,
        enable_polish=enable_polish,
        num_candidates=num_candidates,
        client=client,
        cancel_event=cancel_event,
    )

def _load_demo_input() -> Dict[str, Any]:
    root = _repo_root()
    demo_path = os.path.join(root, "punie_lyric_input.json")
    with open(demo_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {
        "jianpu": str(data.get("jianpu", "")).strip(),
        "mandarin_seed": str(data.get("mandarin_seed", "")).strip(),
        "theme_tags": list(data.get("theme_tags") or []),
        "style_tags": list(data.get("style_tags") or []),
    }


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:
        # 避免把请求头/路径里可能包含的信息打印出来
        super().log_message(format, *args)

    def do_GET(self) -> None:
        if self.path == "/api/demo":
            try:
                demo = _load_demo_input()
                return _json_response(self, 200, {"ok": True, "demo": demo})
            except FileNotFoundError:
                return _json_response(self, 404, {"ok": False, "error": "demo_not_found"})
            except Exception as e:
                return _json_response(self, 400, {"ok": False, "error": str(e)})
        if self.path == "/api/key_status":
            has_key = _try_load_local_api_key() is not None
            return _json_response(self, 200, {"ok": True, "has_key": has_key})
        if self.path == "/api/progress":
            with _STATE_LOCK:
                data = {
                    "ok": True,
                    "running": bool(_RUN_STATE.get("running")),
                    "step": _RUN_STATE.get("step") or "",
                    "last_error": _RUN_STATE.get("last_error"),
                    "logs": list(_RUN_STATE.get("logs") or []),
                    "ts": int(__import__("time").time()),
                }
            return _json_response(self, 200, data)
        if self.path == "/" or self.path.startswith("/?"):
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self) -> None:
        if self.path == "/api/cancel":
            with _STATE_LOCK:
                ev = _RUN_STATE.get("cancel_event")
                if ev is not None:
                    try:
                        ev.set()
                    except Exception:
                        pass
                _RUN_STATE["step"] = "已请求终止…"
            return _json_response(self, 200, {"ok": True})

        if self.path != "/api/run":
            return _json_response(self, 404, {"error": "not_found"})

        api_key = _extract_bearer_token(self.headers.get("Authorization")) or _try_load_local_api_key()
        if not api_key:
            return _json_response(self, 401, {"error": "missing_api_key"})

        payload = _read_json_body(self)

        with _STATE_LOCK:
            if _RUN_STATE.get("running"):
                return _json_response(self, 409, {"ok": False, "error": "already_running"})
            _RUN_STATE["running"] = True
            _RUN_STATE["last_error"] = None
            _RUN_STATE["step"] = "准备中…"
            _RUN_STATE["cancel_event"] = threading.Event()
            _RUN_STATE["logs"] = []

        ph = _ProgressHandler()
        ph.setLevel(logging.INFO)
        root = logging.getLogger()
        root.addHandler(ph)

        try:
            logging.getLogger("frontend").info("收到 /api/run 请求，开始运行流水线")
            with _STATE_LOCK:
                _RUN_STATE["step"] = "开始运行…"
            result = _run_pipeline_from_payload(payload, api_key=api_key)
            logging.getLogger("frontend").info("流水线完成，返回结果")
            with _STATE_LOCK:
                _RUN_STATE["step"] = "完成"
            return _json_response(self, 200, {"ok": True, "result": result})
        except RuntimeError as e:
            if str(e) == "cancelled":
                with _STATE_LOCK:
                    _RUN_STATE["last_error"] = "cancelled"
                    _RUN_STATE["step"] = "已终止"
                return _json_response(self, 499, {"ok": False, "error": "cancelled"})
            raise
        except ModuleNotFoundError as e:
            missing = getattr(e, "name", None) or str(e)
            return _json_response(
                self,
                400,
                {
                    "ok": False,
                    "error": f"缺少依赖：{missing}。请确认运行服务的 python 来自 .venv，并在该环境执行：pip install -r requirements.txt",
                    "python_executable": sys.executable,
                },
            )
        except Exception as e:
            tb = traceback.format_exc(limit=10)
            logging.getLogger("frontend").exception("流水线运行失败")
            with _STATE_LOCK:
                _RUN_STATE["last_error"] = str(e)
                _RUN_STATE["step"] = "运行失败"
            return _json_response(
                self,
                400,
                {
                    "ok": False,
                    "error": str(e),
                    "traceback": tb,
                },
            )
        finally:
            try:
                root.removeHandler(ph)
            except Exception:
                pass
            with _STATE_LOCK:
                _RUN_STATE["running"] = False
                _RUN_STATE["cancel_event"] = None


def main() -> None:
    _force_utf8_stdio()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        force=True,
    )
    web_root = os.path.abspath(os.path.dirname(__file__))
    os.chdir(web_root)

    host = "127.0.0.1"
    port = int(os.environ.get("FRONTEND_PORT", "7860"))

    _auto_kill_port_conflicts(host=host, port=port)

    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"Frontend: http://{host}:{port}", flush=True)
    print(f"Python: {sys.executable}", flush=True)
    print("POST /api/run with Authorization: Bearer <API_KEY>", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()

