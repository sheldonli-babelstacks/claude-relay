"""HTTP 入口：GitHub webhook、Jira webhook、通用 /send、会话自登记 /register、打 tag 的 /tag。单进程，默认只绑 127.0.0.1，
对外经反向代理（见 README／deploy/）。
"""
from __future__ import annotations

import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from .config import Config
from .executor import wake, write_dead_letter
from .logging_setup import body_fingerprint
from .router import Router
from .sources import github as github_source
from .sources import jira as jira_source
from .state import State

logger = logging.getLogger("claude_relay.server")


def make_handler(config: Config, state: State, router: Router):
    from .directory import Directory

    directory = Directory(f"{config.state_dir}/directory.sqlite3")

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
            if self._route_path() == "/directory":
                self._handle_directory()
                return
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
            elif path == "/register":
                self._handle_register()
            elif path == "/tag":
                self._handle_tag()
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

        def _handle_tag(self) -> None:
            from .tagger import TagError, create_tag, write_audit
            from .verify import verify_bearer_token

            ts = config.tag_service
            if ts is None:
                self._respond(404, {"error": "tag service not configured"})
                return
            if not verify_bearer_token(ts.token, self.headers.get("Authorization")):
                self._respond(401, {"error": "bad token"})
                return
            try:
                payload = json.loads(self._read_body() or b"{}")
            except json.JSONDecodeError:
                self._respond(400, {"error": "bad json"})
                return
            audit = {k: str(payload.get(k, ""))[:200] for k in ("from", "repo", "sha", "tag")}
            try:
                result = create_tag(ts, payload)
            except TagError as e:
                logger.warning("tag rejected", extra={"fields": {**audit, "status": e.status, "error": e.message}})
                write_audit(config.state_dir, {**audit, "ok": False, "status": e.status, "error": e.message})
                self._respond(e.status, {"error": e.message})
                return
            logger.info("tag created", extra={"fields": {**audit, "tag_sha": result["tag_sha"]}})
            write_audit(config.state_dir, {**audit, "ok": True, "tag_sha": result["tag_sha"]})
            self._respond(201, result)

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

            # /send 是显式、带令牌认证的点对点消息，不是 webhook 自动触发的——不可能自己
            # 形成回环，所以不过全局熔断那道闸：会话间互相发消息永远可以送达，不会被
            # 别的路由（Jira/GitHub webhook）触发的熔断连带挡住（用户 10-15 明确要求）。
            # 记在 p2p_wakes 供统计，不进熔断计数。
            state.record_p2p_wake(route.name)

            if action_type == "wake":
                account = config.accounts[route.action["account"]]
                session_id = route.action["session_id"]
                result = wake(account, session_id, message)
                if not result.ok:
                    write_dead_letter(
                        f"{config.state_dir}/dead-letter.jsonl", route.name, session_id, message, result.error or ""
                    )
                    self._respond(502, {"error": "wake failed", "detail": result.error})
                    return
                self._respond(200, {"ok": True, "session_id": session_id})
                return

            # send_named：POST body 里的 "to" 是名字，不是 session_id。
            # 投递顺序：自己登记过的协调者 ID（/register）> 静态花名册 > 收件箱 thread；都投不进就排队。
            from .roster import load_roster

            to_name = str(payload.get("to", "")).strip()
            if not to_name:
                self._respond(400, {"error": "to required"})
                return
            roster = load_roster(route.action["roster_path"])
            targets = directory.targets(to_name, roster.get(to_name))
            if not targets:
                self._respond(404, {"error": f"{to_name!r} 不在花名册里，也没登记过"})
                return
            prefix = route.template.get("prefix")
            body_text = f"{prefix}\n{message}" if prefix else message

            errors = []
            for role, acct_alias, session_id in targets:
                account = config.accounts.get(acct_alias)
                if account is None:
                    errors.append(f"{role}: 账号 {acct_alias!r} 没在 relay 配置里")
                    continue
                text = body_text
                if role == "inbox":
                    text = (
                        f"[claude-relay] 收件箱代收：{to_name} 的协调者投不进（ID 可能已换），"
                        f"请转给本 Project 协调者，或直接处理／在本 thread 告诉用户。\n{body_text}"
                    )
                result = wake(account, session_id, text)
                if result.ok:
                    self._respond(200, {"ok": True, "delivered_to": role, "session_id": session_id})
                    return
                errors.append(f"{role} {session_id}: {result.error or ''}"[:300])

            detail = "；".join(errors)
            count = directory.enqueue(to_name, body_text, detail)
            write_dead_letter(
                f"{config.state_dir}/dead-letter.jsonl", route.name, targets[0][2], body_text, detail
            )
            logger.warning("send queued", extra={"fields": {"to": to_name, "pending": count}})
            self._respond(
                202,
                {
                    "ok": False,
                    "queued": True,
                    "pending": count,
                    "detail": detail,
                    "note": f"已排队；{to_name} 下次开工登记（register.py）时会收到",
                },
            )

        def _named_route(self):
            return next((r for r in config.routes if r.action.get("type") == "send_named"), None)

        def _check_named_token(self):
            from .verify import verify_bearer_token

            route = self._named_route()
            token = config.send_tokens.get(route.name) if route else None
            if not token:
                self._respond(404, {"error": "no send_named route configured"})
                return None
            if not verify_bearer_token(token, self.headers.get("Authorization")):
                self._respond(401, {"error": "bad token"})
                return None
            return route

        def _handle_register(self) -> None:
            """会话自己登记当前 ID：{name, session_id, role: coordinator|inbox, account?}。
            回执里带上排队等它的消息，并清空队列。用 send-to-roster 同一把令牌。"""
            from .directory import NAME_RE, ROLES, normalize_session_id
            from .roster import load_roster

            route = self._check_named_token()
            if route is None:
                return
            try:
                payload = json.loads(self._read_body() or b"{}")
            except json.JSONDecodeError:
                self._respond(400, {"error": "bad json"})
                return
            name = str(payload.get("name", "")).strip()
            role = str(payload.get("role", "coordinator")).strip()
            sid = normalize_session_id(str(payload.get("session_id", "")))
            if not NAME_RE.match(name):
                self._respond(400, {"error": "name 要英文字母开头，最多 31 位字母数字 _ -"})
                return
            if role not in ROLES:
                self._respond(400, {"error": f"role 只能是 {'/'.join(ROLES)}"})
                return
            if sid is None:
                self._respond(400, {"error": "session_id 要是 session_… 或 cse_… 形式"})
                return
            static = load_roster(route.action["roster_path"]).get(name) or {}
            live = directory.live(name)
            acct = str(
                payload.get("account") or live.get(role, {}).get("account") or static.get("account") or "b"
            ).lower()
            if acct not in config.accounts:
                self._respond(400, {"error": f"账号 {acct!r} 没在 relay 配置里"})
                return
            previous = live.get(role, {}).get("session_id") or (static.get("session_id") if role == "coordinator" else None)
            directory.register(name, role, acct, sid)
            pending = directory.take(name) if role == "coordinator" or payload.get("take") else []
            logger.info(
                "registered",
                extra={"fields": {"name": name, "role": role, "changed": previous != sid, "delivered": len(pending)}},
            )
            self._respond(
                200,
                {"ok": True, "name": name, "role": role, "session_id": sid, "changed": previous != sid, "pending": pending},
            )

        def _handle_directory(self) -> None:
            """GET /directory：当前生效的通讯录（静态花名册 + 登记覆盖 + 排队数），排错用。"""
            from .roster import load_roster

            route = self._check_named_token()
            if route is None:
                return
            roster = load_roster(route.action["roster_path"])
            live = directory.all_live()
            out = {}
            for name in sorted(set(roster) | set(live)):
                st = roster.get(name) or {}
                lv = live.get(name, {})
                out[name] = {
                    "coordinator": (lv.get("coordinator") or {}).get("session_id") or st.get("session_id"),
                    "coordinator_source": "register" if "coordinator" in lv else "roster.json",
                    "coordinator_updated_at": (lv.get("coordinator") or {}).get("updated_at"),
                    "inbox": (lv.get("inbox") or {}).get("session_id"),
                    "pending": directory.pending_count(name),
                }
            self._respond(200, out)

    return Handler


def run(config: Config, state: State, bind: str = "127.0.0.1", port: int = 8787) -> None:
    router = Router(config, state)
    handler = make_handler(config, state, router)
    httpd = ThreadingHTTPServer((bind, port), handler)
    logger.info("claude-relay listening", extra={"fields": {"bind": bind, "port": port}})
    httpd.serve_forever()
