"""PowerContext-backed NPC memory bridge; no model or substitute memory store."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import os
import re
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig, GetMemoryEntryRequest, RememberMemoryRequest, open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft

ROOT = Path(__file__).resolve().parent


class GameError(ValueError):
    def __init__(self, message: str, status: int = 400):
        self.status = status
        super().__init__(message)


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def text(value, label, maximum):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise GameError(f"{label} 必须是非空字符串，最多 {maximum} 字符")
    return value


def validate_ids(value):
    for name in ("player_id", "turn_id"):
        if not isinstance(value.get(name), str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", value[name]):
            raise GameError(f"{name} 仅支持 1 至 64 位字母、数字、下划线和连字符")
    text(value.get("action"), "action", 400)


class Game:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir.resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.config = BuiltinConfig(database=SQLiteConfig(
            url="sqlite+aiosqlite:///" + (self.data_dir / "npc-memory.db").as_posix()
        ))

    async def memory(self, runtime, player_id):
        scope = await runtime.scopes.create(ScopeDraft(
            title="记仇便利店", summary="独立玩家的店长互动记忆",
            idempotency_key="dify-grudge-shop/" + player_id,
        ))
        return runtime.memory.for_scope(scope.scope_id), scope.scope_id

    async def read_turns(self, memory):
        page = await memory.list()
        turns = []
        for record in page.entries:
            if record.entry.kind == "npc-turn":
                exact = await memory.get(GetMemoryEntryRequest(citation=record.citation))
                turns.append((json.loads(exact.entry.text), record.citation.model_dump(mode="json")))
        turns.sort(key=lambda pair: pair[0]["sequence"])
        return turns

    def audit(self, operation, **fields):
        record = {"time": datetime.now(timezone.utc).isoformat(), "operation": operation, **fields}
        with (self.data_dir / "powercontext-audit.jsonl").open("a", encoding="utf-8") as output:
            output.write(compact(record) + "\n")

    async def prepare(self, request):
        validate_ids(request)
        async with open_builtin_runtime(self.config) as runtime:
            memory, scope_id = await self.memory(runtime, request["player_id"])
            turns = await self.read_turns(memory)
            previous = next((t for t, _ in turns if t["turn_id"] == request["turn_id"]), None)
            if previous and previous["action"] != request["action"]:
                raise GameError("turn_id 已用于另一行动，请换一个 turn_id", 409)
            # Latest twelve records are literal observations, not model-generated player facts.
            recent = turns[-12:]
            history = [{"sequence": t["sequence"], "action": t["action"], "reply": t["reply"]} for t, _ in recent]
            self.audit("read", scope_id=scope_id, player_id=request["player_id"],
                       count=len(turns), citations=[citation for _, citation in recent])
            return {"scope_id": scope_id, "base_sequence": len(turns),
                    "history": compact(history), "remembered_turns": len(turns),
                    "already_committed": previous is not None}

    async def commit(self, request):
        validate_ids(request)
        reply = text(request.get("reply"), "reply", 1400)
        base = request.get("base_sequence")
        if type(base) is not int or base < 0:
            raise GameError("base_sequence 必须是非负整数")
        async with open_builtin_runtime(self.config) as runtime:
            memory, scope_id = await self.memory(runtime, request["player_id"])
            turns = await self.read_turns(memory)
            previous = next(((t, c) for t, c in turns if t["turn_id"] == request["turn_id"]), None)
            if previous:
                if previous[0]["action"] != request["action"]:
                    raise GameError("turn_id 已用于另一行动", 409)
                return {"reply": previous[0]["reply"], "sequence": previous[0]["sequence"],
                        "citation": previous[1], "replayed": True}
            if base != len(turns):
                raise GameError("玩家进度已变化，请重新运行本轮工作流", 409)
            turn = {"player_id": request["player_id"], "turn_id": request["turn_id"],
                    "sequence": base + 1, "action": request["action"], "reply": reply}
            serialized = compact(turn)
            if len(serialized.encode("utf-8")) > 7800:
                raise GameError("互动记录过长，请缩短行动或店长回应")
            result = await memory.remember(RememberMemoryRequest(entries=(MemoryEntryInput(
                kind="npc-turn", text=serialized,
            ),)))
            citation = result.entry.citation.model_dump(mode="json")
            self.audit("write", scope_id=scope_id, player_id=request["player_id"],
                       sequence=base + 1, turn_id=request["turn_id"], citation=citation,
                       content_sha256=hashlib.sha256(serialized.encode()).hexdigest())
            return {"reply": reply, "sequence": base + 1, "citation": citation, "replayed": False}


def serve(host, port, data_dir, token):
    if not token or token == "REPLACE_WITH_A_RANDOM_TOKEN":
        raise SystemExit("先设置 NPC_API_TOKEN 为你自己的随机字符串")
    game, gate = Game(data_dir), threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def respond(self, status, value):
            payload = compact(value).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            self.respond(200 if self.path == "/health" else 404,
                         {"service": "dify-grudge-shop", "status": "ok"} if self.path == "/health" else {"error": "not found"})

        def do_POST(self):
            if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token):
                return self.respond(401, {"error": "invalid token"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 32000:
                    raise GameError("请求大小必须在 1 至 32000 字节之间", 413)
                request = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(request, dict):
                    raise GameError("需要 JSON 对象")
                handler = {"/prepare": game.prepare, "/commit": game.commit}.get(self.path)
                if handler is None:
                    raise GameError("not found", 404)
                # One process owns this DB; a stale parallel workflow is rejected on commit.
                with gate:
                    result = asyncio.run(handler(request))
                self.respond(200, result)
            except GameError as error:
                self.respond(error.status, {"error": str(error)})
            except (UnicodeError, json.JSONDecodeError, ValueError) as error:
                self.respond(400, {"error": str(error)})
            except Exception:
                self.log_error("PowerContext operation failed")
                self.respond(500, {"error": "记忆服务写入或读取失败；本轮未确认保存，请稍后重试"})

    print(f"记仇便利店 memory bridge listening on {host}:{port}", flush=True)
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.getenv("NPC_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("NPC_PORT", "8790")))
    parser.add_argument("--data-dir", type=Path, default=Path(os.getenv("NPC_DATA_DIR", str(ROOT / "data"))))
    args = parser.parse_args()
    serve(args.host, args.port, args.data_dir, os.getenv("NPC_API_TOKEN", ""))
