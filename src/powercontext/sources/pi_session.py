"""Capture immutable visible events from a selected Pi v3 session branch."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, JsonValue

from powercontext.sources.connectors import (
    ConnectorRunCompletion,
    ConnectorRunSession,
    ConnectorRunStatus,
    ConnectorSubmissionStatus,
)
from powercontext.sources.definitions import SourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence


class PiSessionInput(BaseModel):
    content: bytes
    leaf_id: str | None = None


class PiEventInput(BaseModel):
    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


class PiEventSource(Source):
    materialization: Literal[SourceMaterialization.CAPTURED] = SourceMaterialization.CAPTURED
    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _event_identity(session_id: str, event_id: str) -> str:
    # An unambiguous pair encoding keeps even arbitrarily long provider IDs bounded.
    return f"pi:{_digest([session_id, event_id])}"


def _identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _reject_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON constant: {value}")


def _parent_id(record: dict[str, JsonValue]) -> str | None:
    if "parentId" not in record:
        raise ValueError("Pi node is missing parentId")
    parent = record["parentId"]
    if parent is not None and not isinstance(parent, str):
        raise ValueError("parentId must be null or a string")
    return parent


def _parse_archive(content: bytes) -> tuple[str, dict[str, dict[str, JsonValue]]]:
    try:
        records = [
            json.loads(line, parse_constant=_reject_constant)
            for line in content.decode("utf-8-sig", errors="strict").split("\n")
            if line.strip()
        ]
        if not records or not isinstance(records[0], dict):
            raise ValueError("Pi archive must start with a session header")
        header = records[0]
        if header.get("type") != "session" or type(header.get("version")) is not int or header["version"] != 3:
            raise ValueError("Pi archive must use session version 3")
        session_id = _identifier(header.get("id"), "session id")
        nodes: dict[str, dict[str, JsonValue]] = {}
        for record in records[1:]:
            if not isinstance(record, dict):
                raise ValueError("Pi node must be a JSON object")
            event_id = _identifier(record.get("id"), "event id")
            _identifier(record.get("type"), "event type")
            if event_id in nodes:
                raise ValueError("Duplicate Pi event id")
            parent = _parent_id(record)
            if parent not in (None, "") and parent not in nodes:
                raise ValueError("Pi parent must reference a previously appearing node")
            # Requiring a previously appearing parent rules out cycles and forward edges.
            nodes[event_id] = record
        return session_id, nodes
    except (UnicodeError, RecursionError) as error:
        raise ValueError("Invalid Pi archive encoding or nesting") from error


def _visible_text(value: JsonValue, *, tool_calls: bool = False) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    if not isinstance(value, list):
        raise ValueError("Pi content must be a string or a block list")
    parts: list[str] = []
    for block in value:
        if not isinstance(block, dict):
            raise ValueError("Pi content block must be an object")
        if block.get("type") == "text":
            text = block.get("text")
            if not isinstance(text, str):
                raise ValueError("Pi text block must contain a string")
            parts.append(text)
        elif tool_calls and block.get("type") == "toolCall":
            name = _identifier(block.get("name"), "tool name")
            call_id = _identifier(block.get("id"), "tool call id")
            arguments = block.get("arguments", {})
            parts.append(f"[tool_call {name} {call_id}] {_json(arguments)}")
    return "\n".join(parts) if any(parts) else ""


def _event_from_record(session_id: str, record: dict[str, JsonValue]) -> PiEventInput | None:
    event_type = _identifier(record.get("type"), "event type")
    if event_type == "message":
        message = record.get("message")
        if not isinstance(message, dict):
            raise ValueError("Pi message must be an object")
        role = _identifier(message.get("role"), "message role")
        if role == "bashExecution":
            command, output = message.get("command", ""), message.get("output", "")
            if not isinstance(command, str) or not isinstance(output, str):
                raise ValueError("Bash command and output must be strings")
            body = f"$ {command}\n{output}" if command or output else ""
        else:
            body = _visible_text(message.get("content"), tool_calls=role != "toolResult")
            if role == "toolResult" and body:
                name = _identifier(message.get("toolName"), "tool result name")
                call_id = _identifier(message.get("toolCallId"), "tool result call id")
                body = f"[tool_result {name} {call_id}] {body}"
    elif event_type in {"compaction", "branch_summary"}:
        role = event_type
        body = record.get("summary", "")
        if not isinstance(body, str):
            raise ValueError("Pi summary must be a string")
    elif event_type == "custom_message":
        role = "custom"
        body = _visible_text(record.get("content"))
    else:
        return None
    if not body:
        return None
    return PiEventInput(
        session_id=session_id,
        event_id=_identifier(record.get("id"), "event id"),
        parent_id=_parent_id(record),
        event_type=event_type,
        role=role,
        content=f"{role}: {body}",
        record=record,
    )


def _selected_events(
    session_id: str, nodes: dict[str, dict[str, JsonValue]], leaf_id: str | None
) -> tuple[PiEventInput, ...]:
    if leaf_id is None:
        leaf_id = next(reversed(nodes), None)
    elif leaf_id not in nodes:
        raise ValueError("Selected Pi leaf does not exist")
    path: list[dict[str, JsonValue]] = []
    while leaf_id not in (None, ""):
        record = nodes[leaf_id]
        path.append(record)
        leaf_id = _parent_id(record)
    events = []
    for record in reversed(path):
        event = _event_from_record(session_id, record)
        if event is not None:
            events.append(event)
    return tuple(events)


def parse_pi_session(value: PiSessionInput) -> tuple[PiEventInput, ...]:
    session_id, nodes = _parse_archive(value.content)
    return _selected_events(session_id, nodes, value.leaf_id)


def _validate_event(event: PiEventInput) -> None:
    _identifier(event.session_id, "session id")
    _identifier(event.event_id, "event id")
    if _event_from_record(event.session_id, event.record) != event:
        raise ValueError("Pi event fields do not match the original record")


class _PiEventTextProjection:
    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PiEventSource
    output_class = TextEvidence

    def project(self, source: PiEventSource, /) -> TextEvidence:
        return TextEvidence(
            source_type="pi-event",
            source_id=source.name,
            content=source.content,
            metadata={
                "session_id": source.session_id,
                "event_id": source.event_id,
                "parent_id": source.parent_id,
                "event_type": source.event_type,
                "role": source.role,
            },
        )


class _PiEventSourceDefinition:
    name = "pi-event"
    version = "1"
    input_class = PiEventInput
    source_class = PiEventSource
    projections = (_PiEventTextProjection(),)

    async def resolve(self, value: PiEventInput, /) -> PiEventSource:
        _validate_event(value)
        return PiEventSource(name=_event_identity(value.session_id, value.event_id), **value.model_dump())

    async def read(self, source: PiEventSource, /) -> PiEventInput:
        event = PiEventInput.model_validate(source.model_dump())
        _validate_event(event)
        if source.name != _event_identity(event.session_id, event.event_id):
            raise ValueError("Pi Source name does not match its event identity")
        return event


PI_EVENT_SOURCE_DEFINITION: SourceDefinition[PiEventInput, PiEventSource, PiEventInput] = _PiEventSourceDefinition()


class PiSessionConnector:
    name = "pi-session-history"
    version = "1"
    source_definitions = frozenset({"pi-event"})

    def __init__(self, path: str | Path, leaf_id: str | None = None) -> None:
        self.path = Path(path)
        self.leaf_id = leaf_id

    async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
        # Validate the complete snapshot and all historical checks before any submission.
        session_id, nodes = _parse_archive(self.path.read_bytes())
        events = _selected_events(session_id, nodes, self.leaf_id)
        previous = session.checkpoint
        if previous is None:
            imported: dict[str, str] = {}
        else:
            if (
                not isinstance(previous, dict)
                or previous.get("version") != 1
                or previous.get("session_id") != session_id
                or not isinstance(previous.get("imported"), dict)
            ):
                raise ValueError("Checkpoint does not match this Pi session")
            imported = {}
            for event_id, fingerprint in previous["imported"].items():
                if not isinstance(fingerprint, str) or event_id not in nodes or _digest(nodes[event_id]) != fingerprint:
                    raise ValueError("Previously imported Pi record was removed or rewritten")
                imported[event_id] = fingerprint

        next_imported = dict(imported)
        for event in events:
            if event.event_id in imported:
                continue
            outcome = await session.submit(_event_identity(session_id, event.event_id), "pi-event", event)
            if outcome.status is not ConnectorSubmissionStatus.ACCEPTED:
                return ConnectorRunCompletion(status=ConnectorRunStatus.INCOMPLETE, checkpoint=previous)
            next_imported[event.event_id] = _digest(event.record)
        # Lifecycle owns durable checkpoint commit and checks submission outcomes.
        return ConnectorRunCompletion(
            status=ConnectorRunStatus.COMPLETE,
            checkpoint={"version": 1, "session_id": session_id, "imported": next_imported},
        )


__all__ = [
    "PI_EVENT_SOURCE_DEFINITION",
    "PiEventInput",
    "PiEventSource",
    "PiSessionConnector",
    "PiSessionInput",
    "parse_pi_session",
]
