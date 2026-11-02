"""Independent wire validation, receiver reconstruction and Runtime diagnostics."""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from powercontext.builtin.runtime import BuiltinConfig, CaptureSource, PrepareContextRequest, open_builtin_runtime
from powercontext.builtin.runtime.context_pack import (
    ContextPack,
    ContextPackDelta,
    ContextPackError,
    apply_context_pack_delta,
)


@pytest.mark.parametrize("text", ["", "a", "海洋🙂\r\nA\x00e\u0301", "long " * 1000])
@pytest.mark.parametrize("cap", [1, 2, 3, 4, 7, 512])
def test_utf8_boundaries_and_original_bytes(text, cap):
    if any(len(char.encode("utf-8")) > cap for char in text):
        with pytest.raises(ContextPackError):
            ContextPack.from_content(text, chunk_bytes=cap)
        return
    pack = ContextPack.from_content(text, chunk_bytes=cap)
    assert b"".join(pack.items) == text.encode("utf-8")
    assert all(len(item) <= cap and item.decode("utf-8") for item in pack.items)
    assert ContextPack.from_content(text, chunk_bytes=cap) == pack
    assert ContextPack.from_bytes(pack.to_bytes()) == pack


@pytest.mark.parametrize("cap", [0, -1, True, 1.5, "512"])
def test_bad_chunk_limits(cap):
    with pytest.raises(ContextPackError):
        ContextPack.from_content("text", chunk_bytes=cap)


@pytest.mark.parametrize("length", [0, 1, 10, 100, 700, 1000, 10000])
def test_digest_and_self_inclusive_wire_count(length):
    pack = ContextPack.from_content("a" * length)
    payload = json.loads(pack.to_bytes())
    unsigned = {"schema": "powercontext.context-pack.v1", "items": payload["items"]}
    oracle = json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert pack.digest == "sha256:" + hashlib.sha256(oracle).hexdigest()
    assert payload["byte_count"] == pack.byte_count == len(pack.to_bytes())


@pytest.mark.parametrize("field,value", [
    ("schema", "wrong"), ("digest", "sha256:" + "0" * 64),
    ("byte_count", True), ("byte_count", 1.0), ("byte_count", 0),
    ("items", None), ("items", [1]), ("items", ["%%%"]),
    ("items", ["_w=="]), ("items", ["/w=="]), ("items", ["YR=="]),
])
def test_bad_pack_fields(field, value):
    payload = json.loads(ContextPack.build((b"a",)).to_bytes())
    payload[field] = value
    with pytest.raises(ContextPackError):
        ContextPack.from_bytes(json.dumps(payload).encode("utf-8"))


@pytest.mark.parametrize("raw", [b"", b"[]", b"null", b"\xff", b"{broken}", b'{"schema":NaN}'])
def test_bad_json(raw):
    for decoder in (ContextPack.from_bytes, ContextPackDelta.from_bytes):
        with pytest.raises(ContextPackError):
            decoder(raw)


def test_missing_extra_and_duplicate_fields():
    pack = ContextPack.build((b"a",))
    delta = ContextPackDelta.between(pack, ContextPack.build((b"a", b"b")))
    for decoder, wire in ((ContextPack.from_bytes, pack.to_bytes()), (ContextPackDelta.from_bytes, delta.to_bytes())):
        payload = json.loads(wire)
        for key in payload:
            with pytest.raises(ContextPackError):
                decoder(json.dumps({k: v for k, v in payload.items() if k != key}).encode())
        with pytest.raises(ContextPackError):
            decoder(json.dumps({**payload, "extra": 1}).encode())
        with pytest.raises(ContextPackError):
            decoder(wire[:-1] + b',"schema":"duplicate"}')


@pytest.mark.parametrize("prefix", [False, -1, 1.0, "1", None])
def test_invalid_delta_prefix(prefix):
    base = ContextPack.build((b"a",))
    payload = json.loads(ContextPackDelta.between(base, base).to_bytes())
    payload["prefix_items"] = prefix
    with pytest.raises(ContextPackError):
        ContextPackDelta.from_bytes(json.dumps(payload).encode())


def test_no_cached_sender_state_needed_in_another_process():
    base = ContextPack.from_content("shared prefix\n" * 100 + "old")
    target = ContextPack.from_content("shared prefix\n" * 100 + "new\n海洋")
    delta = ContextPackDelta.between(base, target)
    script = """
import base64, json, sys
from powercontext.builtin.runtime.context_pack import ContextPack, ContextPackDelta, apply_context_pack_delta
wire = json.loads(sys.stdin.buffer.read())
base = ContextPack.from_bytes(base64.b64decode(wire['base']))
delta = ContextPackDelta.from_bytes(base64.b64decode(wire['delta']))
sys.stdout.buffer.write(apply_context_pack_delta(base, delta).to_bytes())
"""
    wire = json.dumps({
        "base": base64.b64encode(base.to_bytes()).decode("ascii"),
        "delta": base64.b64encode(delta.to_bytes()).decode("ascii"),
    }).encode()
    result = subprocess.run([sys.executable, "-c", script], input=wire, capture_output=True, check=True)
    assert result.stdout == target.to_bytes()


async def test_empty_runtime_and_authorization_uses_ordinary_prepare():
    async with open_builtin_runtime(BuiltinConfig()) as runtime:
        from powercontext.builtin.scope import ScopeDraft

        scope = await runtime.scopes.create(ScopeDraft(title="Empty", summary="Empty", idempotency_key="empty-pack"))
        context = runtime.context.for_scope(scope.scope_id)
        seen = []

        async def authorize(scopes):
            seen.append(scopes)

        request = PrepareContextRequest(query="absent", max_bytes=4096)
        pack = await context.prepare_incremental(request, authorize_scopes=authorize)
        assert pack.items == () and seen == [(scope.scope_id,)]
        delta = await context.prepare_incremental(request, ContextPack.from_content("old"))
        assert apply_context_pack_delta(ContextPack.from_content("old"), delta).items == ()


async def test_public_two_turn_40_round_diagnostic(capsys):
    """Local diagnostic, NOT the withheld eight-turn official cost sequence."""
    from powercontext.builtin.artifacts.memory import MemoryEntryInput
    from powercontext.builtin.scope import ScopeDraft
    from powercontext.builtin.sources import ContentSource

    sample_path = Path(__file__).resolve().parents[1] / "competition/challenges/context-02-incremental-pack/workload/public/turns.json"
    sample = json.loads(sample_path.read_text(encoding="utf-8"))

    class Pipeline:
        async def extract(self, request):
            return tuple(
                MemoryEntryInput(kind="turn", text=source.content, sources=(source,))
                for source in request.sources if isinstance(source, ContentSource)
            )

    raw_bytes = incremental_bytes = full_pack_bytes = gzip_bytes = 0
    distinct = set()
    receiver = None
    async with open_builtin_runtime(BuiltinConfig(), candidate_pipeline=Pipeline()) as runtime:
        scope = await runtime.scopes.create(ScopeDraft(title="Diagnostic", summary="Diagnostic", idempotency_key="pack-cost"))
        sources = runtime.sources.for_scope(scope.scope_id)
        memory = runtime.memory.for_scope(scope.scope_id)
        context = runtime.context.for_scope(scope.scope_id)
        for index, turn in enumerate(sample["turns"]):
            await sources.capture(CaptureSource(source_id=f"initial-{index}", content=turn["text"], metadata={}))
        while (await memory.flush()).processed:
            pass
        for round_index in range(40):
            if round_index in (10, 20, 30):
                # Replay an original public turn as a separately captured source.
                # This adds a new citation; it does not manufacture new meeting text.
                turn = sample["turns"][(round_index // 10) % len(sample["turns"])]
                await sources.capture(CaptureSource(source_id=f"追加-{round_index}", content=turn["text"], metadata={}))
                while (await memory.flush()).processed:
                    pass
            query = sample["query"] if round_index < 20 else "controls"
            request = PrepareContextRequest(query=query, max_bytes=4096)
            expected = await context.prepare(request)
            assert expected.content
            content = expected.content.encode("utf-8")
            distinct.add(content)
            transport = await context.prepare_incremental(request, receiver)
            wire = transport.to_bytes()
            if receiver is None:
                receiver = ContextPack.from_bytes(wire)
            else:
                receiver = apply_context_pack_delta(receiver, ContextPackDelta.from_bytes(wire))
            assert b"".join(receiver.items) == content
            assert receiver == ContextPack.from_content(expected.content)
            raw_bytes += len(content)
            incremental_bytes += len(wire)
            full_pack_bytes += len(receiver.to_bytes())
            gzip_bytes += len(gzip.compress(content, compresslevel=9, mtime=0))
    assert len(distinct) >= 4
    metrics = {
        "sequence": "local-public-two-turn-diagnostic",
        "rounds": 40,
        "distinct_contexts": len(distinct),
        "raw_full_bytes": raw_bytes,
        "incremental_bytes": incremental_bytes,
        "full_pack_bytes": full_pack_bytes,
        "gzip_bytes": gzip_bytes,
        "saving_vs_raw": 1 - incremental_bytes / raw_bytes,
        "saving_vs_full_pack": 1 - incremental_bytes / full_pack_bytes,
        "saving_vs_gzip": 1 - incremental_bytes / gzip_bytes,
    }
    with capsys.disabled():
        print(json.dumps(metrics, sort_keys=True), flush=True)
