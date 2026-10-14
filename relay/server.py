"""HTTP 入口：GitHub webhook、Jira webhook、通用 /send。单进程，默认只绑 127.0.0.1，
对外经反向代理（见 README／deploy/）。
"""
from __future__ import annotations

import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from .antiloop import CircuitBreakerOpen, check_and_record_wake
from .config import Config
from .executor import wake, write_dead_letter
from .logging_setup import body_fingerprint
from .router import Router
from .sources import github as github_source
from .sources import jira as jira_source
from .state import State

logger = logging.getLogger("claude_relay.server")


def make_handler(config: Config, state: State, router: Router):
    class Handler(BaseHTTPRequestHandler):
        server_version = "claude-relay/0.1"

        def log_message(self, format: str, *args) -> None:  # noqa: A002
            pass  # 用 relay 自己的 JSON 日志，关掉 http.server 默认的明文访问日志

        def _read_body(self) -> bytes:
            length = int(self.headers.get("Content-Length", 0))
            return self.rfile.read(length) if length else b""

        def _respond(self, code: int, body: dict | None = None) -> None:
            payload = json.dumps(body or {}).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _route_path(self) -> str:
            """self.path 是原始 request-target，带 query string（Jira 原生 webhook 会自带
            ?triggeredByUser=... 之类的参数）——路由判断只看路径部分。"""
            return urlsplit(self.path).path

        def do_GET(self) -> None:  # noqa: N802
            if self._route_path() == "/healthz":
                tripped = state.circuit_breaker_tripped()
                self._respond(200, {"ok": True, "circuit_breaker_tripped": tripped})
                return
            self._respond(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            path = self._route_path()
            if path == "/hook/github":
                self._handle_github()
            elif path == "/hook/jira":
                self._handle_jira()
            elif path.startswith("/send/"):
                self._handle_send(path[len("/send/"):])
            else:
                self._respond(404, {"error": "not found"})

        def _handle_github(self) -> None:
            body = self._read_body()
            if not config.github_webhook_secret:
                self._respond(500, {"error": "server not configured"})
                return
            from .verify import verify_hmac_sha256

            sig = self.headers.get("X-Hub-Signature-256")
            if not verify_hmac_sha256(config.github_webhook_secret, body, sig):
                logger.warning("github signature invalid", extra={"fields": body_fingerprint(body)})
                self._respond(401, {"error": "bad signature"})
                return

            event_type = self.headers.get("X-GitHub-Event", "")
            delivery_id = self.headers.get("X-GitHub-Delivery", "")
            try:
                payload = json.loads(body or b"{}")
            except json.JSONDecodeError:
                self._respond(400, {"error": "bad json"})
                return

            ev = github_source.parse(event_type, delivery_id, payload)
            if ev is None:
                self._respond(204)
                return
            router.handle(ev)
            self._respond(204)

        def _handle_jira(self) -> None:
            body = self._read_body()
            if not config.jira_webhook_secret:
                self._respond(500, {"error": "server not configured"})
                return
            from .verify import verify_hmac_sha256

            sig = self.headers.get("X-Hub-Signature")
            if not verify_hmac_sha256(config.jira_webhook_secret, body, sig):
                logger.warning("jira signature invalid", extra={"fields": body_fingerprint(body)})
                self._respond(401, {"error": "bad signature"})
                return

            try:
                payload = json.loads(body or b"{}")
            except json.JSONDecodeError:
                self._respond(400, {"error": "bad json"})
                return

            ev = jira_source.parse(payload, site_base_url=None)
            if ev is None:
                self._respond(204)
                return
            router.handle(ev)
            self._respond(204)

        def _handle_send(self, route_name: str) -> None:
            from .verify import verify_bearer_token

            token = config.send_tokens.get(route_name)
            if not token:
                self._respond(404, {"error": "unknown route"})
                return
            if not verify_bearer_token(token, self.headers.get("Authorization")):
                self._respond(401, {"error": "bad token"})
                return

            body = self._read_body()
            try:
                payload = json.loads(body or b"{}")
            except json.JSONDecodeError:
                self._respond(400, {"error": "bad json"})
                return
            message = str(payload.get("message", ""))[:4000]
            if not message:
                self._respond(400, {"error": "message required"})
                return

            route = next((r for r in config.routes if r.name == route_name), None)
            action_type = route.action.get("type") if route else None
            if route is None or action_type not in ("wake", "send_named"):
                self._respond(404, {"error": "unknown route"})
                return

            if action_type == "wake":
                account = config.accounts[route.action["account"]]
                session_id = route.action["session_id"]
            else:  # send_named：POST body 里的 "to" 是花名册里的姓名，不是 session_id
                from .roster import load_roster

                to_name = str(payload.get("to", "")).strip()
                if not to_name:
                    self._respond(400, {"error": "to required"})
                    return
                roster = load_roster(route.action["roster_path"])
                entry = roster.get(to_name)
                if entry is None:
                    self._respond(404, {"error": f"{to_name!r} 不在花名册里"})
                    return
                acct_alias = entry.get("account", "a")
                account = config.accounts.get(acct_alias)
                if account is None:
                    self._respond(500, {"error": f"{to_name} 的账号 {acct_alias!r} 没在 relay 配置里"})
                    return
                session_id = entry["session_id"]
                prefix = route.template.get("prefix")
                if prefix:
                    message = f"{prefix}\n{message}"

            try:
                check_and_record_wake(state, route.name, config.anti_loop)
            except CircuitBreakerOpen as e:
                self._respond(503, {"error": "circuit breaker open", "reason": str(e)})
                return

            result = wake(account, session_id, message)
            if not result.ok:
                write_dead_letter(
                    f"{config.state_dir}/dead-letter.jsonl", route.name, session_id, message, result.error or ""
                )
                self._respond(502, {"error": "wake failed", "detail": result.error})
                return
            self._respond(200, {"ok": True, "session_id": session_id})

    return Handler


def run(config: Config, state: State, bind: str = "127.0.0.1", port: int = 8787) -> None:
    router = Router(config, state)
    handler = make_handler(config, state, router)
    httpd = ThreadingHTTPServer((bind, port), handler)
    logger.info("claude-relay listening", extra={"fields": {"bind": bind, "port": port}})
    httpd.serve_forever()
