"""Lifecycle recovery through real SQLite failures and public Runtime reads."""

from __future__ import annotations

import sqlite3

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.records import ArtifactWrite
from powercontext.builtin.runtime import (
    BuiltinConfig, CaptureSource, GetMemoryEntryRequest, GetTopicMemoryRequest, RememberMemoryRequest,
    SearchMemoryRequest, SearchTopicMemoryRequest, open_builtin_runtime,
)
from powercontext.builtin.runtime.source_lifecycle import SourceLifecycleError, SourceLifecycleEvent
from powercontext.builtin.scope import ScopeDraft
from powercontext.sources import SourceRef


def event(**changes):
    return SourceLifecycleEvent(**(dict(node_id="root", revision=3, sequence=1,
                                       available=False, reason="withdraw") | changes))


@pytest.mark.parametrize("changes", [
    {"revision": True}, {"revision": "3"}, {"revision": 3.0}, {"revision": 0},
    {"sequence": False}, {"sequence": "1"}, {"sequence": 1.0}, {"sequence": -1},
    {"available": 0}, {"available": "false"}, {"node_id": "  "}, {"node_id": 8},
    {"reason": "\t\n"}, {"reason": None}, {"extra": 1},
])
def test_event_has_strict_types_and_nonblank_identity(changes):
    with pytest.raises(ValidationError):
        event(**changes)


async def graph(runtime, key):
    scope = (await runtime.scopes.create(ScopeDraft(title=key, summary=key, idempotency_key=key))).scope_id
    service = runtime.lifecycle.for_scope(scope)
    source = (await runtime.sources.for_scope(scope).capture(
        CaptureSource(source_id="origin", content="original evidence", metadata={})
    )).source_ref
    await service.register_source("root", source, revision=3)
    citations = []
    for index in range(3):
        citation = (await runtime.memory.for_scope(scope).remember(RememberMemoryRequest(
            entries=(MemoryEntryInput(kind="fact", text=f"searchablefact{index} historical body"),)
        ))).entry.citation
        await service.register_memory(f"fact{index}", citation, parents=("root",), policy="all")
        citations.append(citation)
    created = await runtime.records.for_scope(scope).create_artifact("topic-memory", ArtifactWrite(content={
        "title": "summaryneedle", "summary": "summaryneedle original summary", "detail": "complete detail"
    }))
    topic = ArtifactRef(family="topic-memory", artifact_id=created.artifact_id, revision=created.revision)
    await service.register_topic("topic", topic, parents=("fact0", "fact1"), policy="any")
    return scope, service, source, citations, topic


async def assert_states(runtime, scope, state):
    result = await runtime.memory.for_scope(scope).list(include_inactive=True)
    assert len(result.entries) == 3
    assert {item.state for item in result.entries} == {state}
    results = await runtime.memory.for_scope(scope).search(SearchMemoryRequest(
        query="searchablefact0", mode="fts", limit=10
    ))
    assert bool(results.hits) == (state == "active")


async def test_partial_native_write_failure_restarts_and_retries_without_duplicate_revisions(tmp_path):
    path = tmp_path / "lifecycle.db"
    config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{path}"))
    async with open_builtin_runtime(config) as runtime:
        scope, lifecycle, _, citations, topic = await graph(runtime, "recovery")
        head = (await runtime.memory.for_scope(scope).list()).memory_ref.revision
        # Permit the first native state commit, then abort the second one.
        with sqlite3.connect(path) as connection:
            connection.execute(
                "CREATE TRIGGER fail_lifecycle BEFORE INSERT ON pc_artifacts "
                f"WHEN NEW.family='memory' AND NEW.revision > {head + 1} "
                "BEGIN SELECT RAISE(ABORT, 'controlled lifecycle failure'); END"
            )
        with pytest.raises(IntegrityError):
            await lifecycle.apply(event())
        rows = (await runtime.memory.for_scope(scope).list(include_inactive=True)).entries
        assert sum(item.state == "inactive" for item in rows) == 1
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TRIGGER fail_lifecycle")
    async with open_builtin_runtime(config) as runtime:
        lifecycle = runtime.lifecycle.for_scope(scope)
        assert await lifecycle.apply(event()) == []
        await assert_states(runtime, scope, "inactive")
        for citation in citations:
            original = await runtime.memory.for_scope(scope).get(GetMemoryEntryRequest(citation=citation))
            assert original.entry.entry_version_id == citation.entry_version_id
        assert await lifecycle.resolve("topic", purpose="audit") == topic
        assert not await runtime.topic_memory.for_scope(scope).browse(limit=20)
        historical_topic = await runtime.topic_memory.for_scope(scope).get(GetTopicMemoryRequest(artifact=topic))
        assert historical_topic.topic.content.summary == "summaryneedle original summary"
        withdrawn_head = (await runtime.memory.for_scope(scope).list()).memory_ref
        await lifecycle.apply(event())
        assert (await runtime.memory.for_scope(scope).list()).memory_ref == withdrawn_head
        assert await lifecycle.apply(event(sequence=2, available=True, reason="restore")) == [
            "fact0", "fact1", "fact2", "root", "topic"
        ]
        await assert_states(runtime, scope, "active")
        assert (await runtime.topic_memory.for_scope(scope).browse(limit=20))[0].artifact_ref == topic
        for index, citation in enumerate(citations):
            assert await lifecycle.resolve(f"fact{index}", purpose="generation") == citation
        before = (await runtime.memory.for_scope(scope).list()).memory_ref
        await lifecycle.apply(event(sequence=2, available=True, reason="restore"))
        assert (await runtime.memory.for_scope(scope).list()).memory_ref == before


async def test_scopes_are_isolated_and_bad_bindings_cannot_mutate_graph():
    async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
        a, service, source, citations, _ = await graph(runtime, "first")
        b, other, _, _, _ = await graph(runtime, "second")
        await service.register_source("root", source, revision=3)
        await service.register_memory("fact0", citations[0], parents=("root",))
        invalid = [
            service.register_memory("bad", citations[0], parents=("missing",)),
            service.register_memory("bad", citations[0], parents=("root", "root")),
            service.register_memory("bad", citations[0], parents=()),
            service.register_memory("bad", citations[0], parents=("bad",)),
            service.register_memory("bad", citations[0], parents=("root",), policy="unknown"),
            service.register_source("root", source, revision=4),
            service.register_source("missing", SourceRef(source_type="content", source_id="missing"), revision=1),
            other.register_memory("foreign", citations[0], parents=("root",)),
            service.resolve("missing", purpose="audit"),
            service.resolve("root", purpose="unknown"),
            service.apply(event(node_id="fact0")),
        ]
        for operation in invalid:
            with pytest.raises(SourceLifecycleError):
                await operation
        await service.apply(event(reason="withdraw" * 1000))
        await assert_states(runtime, a, "inactive")
        await assert_states(runtime, b, "active")
        with pytest.raises(SourceLifecycleError):
            await service.apply(event(reason="same sequence, different reason"))
        results = await runtime.topic_memory.for_scope(a).search(SearchTopicMemoryRequest(query="summaryneedle", limit=10))
        assert not results.hits
        results = await runtime.topic_memory.for_scope(b).search(SearchTopicMemoryRequest(query="summaryneedle", limit=10))
        assert results.hits
