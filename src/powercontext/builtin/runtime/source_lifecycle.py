"""Persistent evidence dependencies and reversible native Memory eligibility."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import insert, select, update

from powercontext.artifacts import ArtifactRef, MemoryCitation
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.tables import SOURCE_LIFECYCLE_TABLE
from powercontext.builtin.sources import validate_scope_id
from powercontext.sources import SourceRef

if TYPE_CHECKING:
    from powercontext.builtin.runtime.application import BuiltinRuntime


class SourceLifecycleError(ValueError):
    """Invalid lifecycle identity, graph, event or intended use."""


class SourceLifecycleEvent(BaseModel):
    """One immutable, monotonically sequenced source availability event."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    node_id: str
    revision: int = Field(gt=0)
    sequence: int = Field(gt=0)
    available: bool
    reason: str

    @field_validator("node_id", "reason")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _node_id(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SourceLifecycleError("node_id must be a nonblank string")
    return value


class SourceLifecycleApplication:
    """Select persistent evidence lifecycle services by scope."""

    def __init__(self, runtime: BuiltinRuntime, database: AsyncDatabase | None) -> None:
        self._runtime = runtime
        self._database = database

    def for_scope(self, scope_id: str, /) -> ScopedSourceLifecycle:
        if self._database is None:
            raise SourceLifecycleError("lifecycle requires relational persistence")
        return ScopedSourceLifecycle(self._runtime, self._database, validate_scope_id(scope_id))


class ScopedSourceLifecycle:
    """An immutable dependency DAG with durable event intent and retryable projections.

    Event intent is committed before changing native Memory. Replays reconcile
    actual manifest states, so a failed native write can be retried even after
    restart, while successful replay creates no additional Memory revision.
    """

    def __init__(self, runtime: BuiltinRuntime, database: AsyncDatabase, scope_id: str) -> None:
        self._runtime = runtime
        self._database = database
        self.scope_id = scope_id

    async def _nodes(self) -> dict[str, dict]:
        async with self._database.transaction() as connection:
            rows = (
                await connection.execute(
                    select(SOURCE_LIFECYCLE_TABLE).where(SOURCE_LIFECYCLE_TABLE.c.scope_id == self.scope_id)
                )
            ).mappings()
            return {row["node_id"]: dict(row) for row in rows}

    async def register_source(self, node_id: str, source_ref: SourceRef, *, revision: int) -> None:
        if type(revision) is not int or revision < 1:
            raise SourceLifecycleError("revision must be a positive integer")
        await self._register(node_id, "source", source_ref, (), "all", revision)

    async def register_memory(
        self, node_id: str, citation: MemoryCitation, *, parents: tuple[str, ...], policy: str = "all"
    ) -> None:
        await self._register(node_id, "memory", citation, parents, policy, 0)

    async def register_topic(
        self, node_id: str, artifact_ref: ArtifactRef, *, parents: tuple[str, ...], policy: str = "all"
    ) -> None:
        await self._register(node_id, "topic", artifact_ref, parents, policy, 0)

    async def _register(
        self,
        node_id: str,
        kind: str,
        reference: SourceRef | MemoryCitation | ArtifactRef,
        parents: tuple[str, ...],
        policy: str,
        revision: int,
    ) -> None:
        _node_id(node_id)
        expected = {"source": SourceRef, "memory": MemoryCitation, "topic": ArtifactRef}[kind]
        if not isinstance(reference, expected):
            raise SourceLifecycleError("invalid reference type")
        if policy not in ("all", "any"):
            raise SourceLifecycleError("unknown dependency policy")
        if isinstance(parents, (str, bytes)):
            raise SourceLifecycleError("parents must be a collection of node IDs")
        try:
            parents = tuple(_node_id(parent) for parent in parents)
        except TypeError as error:
            raise SourceLifecycleError("invalid parents") from error
        if kind != "source" and (not parents or len(set(parents)) != len(parents) or node_id in parents):
            raise SourceLifecycleError("parents must be nonempty, unique and exclude self")
        # all/any dependencies are sets: parent ordering does not alter a binding.
        parents = tuple(sorted(parents))
        binding = {
            "kind": kind,
            "reference": _json(reference.model_dump(mode="json")),
            "parents": _json(parents),
            "policy": policy,
            "revision": revision,
        }
        async with self._runtime._scope_operation(self.scope_id), self._runtime._locked(self.scope_id):
            nodes = await self._nodes()
            if node_id in nodes:
                if any(nodes[node_id][key] != value for key, value in binding.items()):
                    raise SourceLifecycleError("node already has a different binding")
                await self._propagate(nodes)
                return
            if any(parent not in nodes for parent in parents):
                raise SourceLifecycleError("all parents must already be registered")
            await self._validate_reference(kind, reference)
            node = {
                "scope_id": self.scope_id,
                "node_id": node_id,
                **binding,
                "available": True,
                "last_event": None,
                "artifact_id": reference.artifact_id if isinstance(reference, ArtifactRef) else None,
            }
            nodes[node_id] = node
            self._evaluate(nodes)
            async with self._database.transaction() as connection:
                await connection.execute(insert(SOURCE_LIFECYCLE_TABLE).values(**node))
            await self._propagate(nodes)

    async def _validate_reference(self, kind: str, reference: Any) -> None:
        from powercontext.builtin.runtime.models import GetMemoryEntryRequest

        try:
            if kind == "source":
                await self._runtime.records.for_scope(self.scope_id).get_source(
                    reference.source_type, reference.source_id
                )
            elif kind == "memory":
                await self._runtime.memory.for_scope(self.scope_id).get(GetMemoryEntryRequest(citation=reference))
            else:
                if reference.family != "topic-memory":
                    raise SourceLifecycleError("expected a Topic Memory reference")
                await self._runtime.records.for_scope(self.scope_id).get_artifact_revision(
                    reference.family, reference.artifact_id, reference.revision
                )
        except Exception as error:
            raise SourceLifecycleError("reference must exist and be readable in this scope") from error

    @staticmethod
    def _evaluate(nodes: dict[str, dict]) -> None:
        visited: set[str] = set()
        visiting: set[str] = set()

        def visit(node_id: str) -> bool:
            if node_id in visiting:
                raise SourceLifecycleError("cyclic dependencies")
            if node_id in visited:
                return nodes[node_id]["available"]
            visiting.add(node_id)
            node = nodes[node_id]
            if node["kind"] != "source":
                support = [visit(parent) for parent in json.loads(node["parents"])]
                node["available"] = all(support) if node["policy"] == "all" else any(support)
            visiting.remove(node_id)
            visited.add(node_id)
            return node["available"]

        for node_id in nodes:
            visit(node_id)

    async def apply(self, event: SourceLifecycleEvent) -> list[str]:
        if not isinstance(event, SourceLifecycleEvent):
            raise SourceLifecycleError("expected SourceLifecycleEvent")
        async with self._runtime._scope_operation(self.scope_id), self._runtime._locked(self.scope_id):
            nodes = await self._nodes()
            node = nodes.get(event.node_id)
            if node is None or node["kind"] != "source" or node["revision"] != event.revision:
                raise SourceLifecycleError("unknown Source or lifecycle revision mismatch")
            payload = _json(event.model_dump(mode="json"))
            if node["last_event"] is not None:
                previous = json.loads(node["last_event"])
                if event.sequence < previous["sequence"] or (
                    event.sequence == previous["sequence"] and payload != node["last_event"]
                ):
                    raise SourceLifecycleError("obsolete sequence or conflicting event replay")
            node["available"] = event.available
            node["last_event"] = payload
            self._evaluate(nodes)
            # Persist all desired states and the event together. Native writes
            # below intentionally have their own existing transaction boundaries.
            async with self._database.transaction() as connection:
                for current in nodes.values():
                    await connection.execute(
                        update(SOURCE_LIFECYCLE_TABLE)
                        .where(
                            SOURCE_LIFECYCLE_TABLE.c.scope_id == self.scope_id,
                            SOURCE_LIFECYCLE_TABLE.c.node_id == current["node_id"],
                        )
                        .values(available=current["available"], last_event=current["last_event"])
                    )
            await self._propagate(nodes)
            return sorted(key for key, current in nodes.items() if current["available"])

    async def _propagate(self, nodes: dict[str, dict]) -> None:
        async with self._runtime._context(self.scope_id) as context:
            service = context.artifacts.memory
            for node in nodes.values():
                if node["kind"] != "memory":
                    continue
                citation = MemoryCitation.model_validate_json(node["reference"])
                current = await service.head(citation.memory_ref.artifact_id)
                item = next(
                    (item for item in current.content.manifest.entries if item.entry_id == citation.entry_id),
                    None,
                )
                if item is None or item.entry_version_id != citation.entry_version_id:
                    raise SourceLifecycleError("registered Memory body version changed")
                desired = "active" if node["available"] else "inactive"
                if item.state != desired:
                    entry = await service.validate_citation(citation)
                    transition = service.reactivate if node["available"] else service.forget
                    # The complete caller reason is retained in last_event;
                    # native Memory reasons have a separate 512-character limit.
                    await transition(current, entries=(entry,), reason="source lifecycle availability update")

    async def resolve(self, node_id: str, *, purpose: str) -> SourceRef | MemoryCitation | ArtifactRef:
        _node_id(node_id)
        if purpose not in ("generation", "audit"):
            raise SourceLifecycleError("unknown reference purpose")
        async with self._runtime._scope_operation(self.scope_id), self._runtime._locked(self.scope_id):
            nodes = await self._nodes()
            node = nodes.get(node_id)
            if node is None:
                raise SourceLifecycleError("unknown node")
            if purpose == "generation" and not node["available"]:
                raise SourceLifecycleError("node is unavailable for generation")
            model = {"source": SourceRef, "memory": MemoryCitation, "topic": ArtifactRef}[node["kind"]]
            return model.model_validate_json(node["reference"])
