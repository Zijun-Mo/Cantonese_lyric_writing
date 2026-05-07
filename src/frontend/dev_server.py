"""最小依赖的本地前端 + API 运行服务（仅标准库）。

用法：
  python src/frontend/dev_server.py

访问：
  http://127.0.0.1:7860
"""

from __future__ import annotations

import json
import os
import sys
import threading
import traceback
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Tuple


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


def _json_response(handler: SimpleHTTPRequestHandler, status: int, data: Dict[str, Any]) -> None:
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


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
    return run_pipeline(
        lyric_input,
        enable_polish=enable_polish,
        num_candidates=num_candidates,
        client=client,
    )


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:
        # 避免把请求头/路径里可能包含的信息打印出来
        super().log_message(format, *args)

    def do_GET(self) -> None:
        if self.path == "/" or self.path.startswith("/?"):
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self) -> None:
        if self.path != "/api/run":
            return _json_response(self, 404, {"error": "not_found"})

        api_key = _extract_bearer_token(self.headers.get("Authorization"))
        if not api_key:
            return _json_response(self, 401, {"error": "missing_api_key"})

        payload = _read_json_body(self)

        try:
            result = _run_pipeline_from_payload(payload, api_key=api_key)
            return _json_response(self, 200, {"ok": True, "result": result})
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
            return _json_response(
                self,
                400,
                {
                    "ok": False,
                    "error": str(e),
                    "traceback": tb,
                },
            )


def main() -> None:
    web_root = os.path.abspath(os.path.dirname(__file__))
    os.chdir(web_root)

    host = "127.0.0.1"
    port = int(os.environ.get("FRONTEND_PORT", "7860"))

    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"Frontend: http://{host}:{port}", flush=True)
    print(f"Python: {sys.executable}", flush=True)
    print("POST /api/run with Authorization: Bearer <API_KEY>", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()

