"""Pi evidence contracts using real Source registry and ConnectorLifecycle.

The in-memory sink/checkpoint fixture verifies lifecycle semantics, not native
Runtime persistence. The competition acceptance suite covers that boundary.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from powercontext.sources import (
    TEXT_EVIDENCE_PROJECTION_KEY,
    ConnectorBinding,
    SourceDefinitionRegistry,
    SourceObservation,
    SourceRef,
    manifest_for_definition,
    project_source_for_transport,
)
from powercontext.sources.connectors import ConnectorLifecycle, ConnectorRunStatus
from powercontext.sources.pi_session import (
    PI_EVENT_SOURCE_DEFINITION,
    PiSessionConnector,
    PiSessionInput,
    parse_pi_session,
)


def archive(records, session_id="session"):
    return "\n".join(
        json.dumps(record, ensure_ascii=False)
        for record in [{"type": "session", "version": 3, "id": session_id}, *records]
    ).encode("utf-8")


def message(event_id, parent, text, role="user"):
    return {
        "type": "message", "id": event_id, "parentId": parent,
        "message": {"role": role, "content": text},
    }


class PiParserTests(unittest.TestCase):
    def parse(self, records, leaf=None):
        return parse_pi_session(PiSessionInput(content=archive(records), leaf_id=leaf))

    def test_branches_and_control_nodes(self):
        records = [
            message("root", None, "共同开头"),
            message("left", "root", "左边"),
            {"type": "model_change", "id": "control", "parentId": "root"},
            message("right", "control", "右边"),
        ]
        self.assertEqual([e.event_id for e in self.parse(records, "left")], ["root", "left"])
        events = self.parse(records)
        self.assertEqual([e.event_id for e in events], ["root", "right"])
        self.assertEqual(events[1].parent_id, "control")
        self.assertEqual(events[1].record, records[3])

    def test_visible_formats_and_original_records(self):
        records = [
            message("a", None, [
                {"type": "thinking", "thinking": "secret"},
                {"type": "text", "text": "  text  "},
                {"type": "image", "data": "not text"},
                {"type": "toolCall", "name": "read", "id": "call", "arguments": {"z": 1, "a": "中文"}},
            ], "assistant"),
            {
                "type": "message", "id": "b", "parentId": "a",
                "message": {"role": "toolResult", "toolName": "read", "toolCallId": "call",
                            "content": [{"type": "text", "text": "line 1"}, {"type": "text", "text": "line 2"}]},
            },
            {"type": "compaction", "id": "c", "parentId": "b", "summary": "compact"},
            {"type": "branch_summary", "id": "d", "parentId": "c", "summary": "branch"},
            {"type": "custom_message", "id": "e", "parentId": "d", "content": "custom"},
            {"type": "message", "id": "f", "parentId": "e",
             "message": {"role": "bashExecution", "command": "echo x", "output": "x\n"}},
        ]
        events = self.parse(records)
        self.assertEqual([e.content for e in events], [
            'assistant:   text  \n[tool_call read call] {"a":"中文","z":1}',
            "toolResult: [tool_result read call] line 1\nline 2",
            "compaction: compact", "branch_summary: branch", "custom: custom",
            "bashExecution: $ echo x\nx\n",
        ])
        self.assertEqual([e.record for e in events], records)

    def test_bom_and_unicode_separators(self):
        raw = b"\xef\xbb\xbf" + archive([message("a", None, "a\u2028b\u0085c")])
        events = parse_pi_session(PiSessionInput(content=raw))
        self.assertEqual(events[0].content, "user: a\u2028b\u0085c")

    def test_empty_and_invisible_branches(self):
        self.assertEqual(self.parse([]), ())
        self.assertEqual(self.parse([message("a", None, [{"type": "thinking", "thinking": "x"}])]), ())
        self.assertEqual(self.parse([message("a", None, "")]), ())
        self.assertEqual(self.parse([message("a", None, " ")])[0].content, "user:  ")

    def test_invalid_graphs_and_off_branch_corruption(self):
        invalid = [
            [{"type": "model_change", "id": "a"}],
            [{"type": "model_change", "id": "a", "parentId": "missing"}],
            [{"type": "model_change", "id": "a", "parentId": "a"}],
            [message("a", None, "x"), message("a", None, "y")],
            [message("a", None, "x"), {"type": "model_change", "id": "b", "parentId": []}],
        ]
        for records in invalid:
            with self.subTest(records=records), self.assertRaises(ValueError):
                self.parse(records, "a")
        with self.assertRaises(ValueError):
            self.parse([message("a", None, "x")], "missing")

    def test_invalid_json_and_headers(self):
        for raw in (b"", b"[]", b"null", b"{}", b"\xff", b'{"type":"session","version":2,"id":"s"}',
                    archive([message("a", None, "ok")]) + b"\n{broken"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_pi_session(PiSessionInput(content=raw))


class DurableFixture:
    """Shared test state; deepcopy load simulates a freshly read checkpoint."""

    def __init__(self):
        self.registry = SourceDefinitionRegistry((PI_EVENT_SOURCE_DEFINITION,))
        self.sources = {}
        self.value = None
        self.fail_id = None
        self.reject_id = None

    async def load(self, binding):
        return deepcopy(self.value)

    async def save(self, binding, checkpoint, *, expected):
        if self.value != expected:
            raise AssertionError("checkpoint comparison failed")
        self.value = deepcopy(checkpoint)

    async def submit(self, binding, item_id, definition_name, value):
        from powercontext.errors import ConnectorSubmissionRejectedError

        if value.event_id == self.fail_id:
            raise OSError("simulated ingestion failure")
        if value.event_id == self.reject_id:
            raise ConnectorSubmissionRejectedError("simulated rejection")
        observation = project_source_for_transport(self.registry, await self.registry.resolve(value))
        if item_id in self.sources and self.sources[item_id] != observation:
            raise ValueError("changed immutable source")
        self.sources[item_id] = observation
        return SourceRef(source_type=definition_name, source_id=item_id)


class PiLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "session.jsonl"
        self.fixture = DurableFixture()
        self.binding = ConnectorBinding(
            scope_id="s", binding_id="pi", connector_name="pi-session-history", connector_version="1"
        )
        self.records = [message("a", None, "root"), message("left", "a", "left"), message("right", "a", "right")]

    async def run_leaf(self, leaf):
        return await ConnectorLifecycle(sink=self.fixture, checkpoints=self.fixture).run(
            PiSessionConnector(self.path, leaf), self.binding
        )

    def write(self):
        self.path.write_bytes(archive(self.records))

    async def test_registry_large_event_roundtrip(self):
        text = "完整工具输出\n" * 10000
        records = [{"type": "message", "id": "tool", "parentId": None,
                    "message": {"role": "toolResult", "toolName": "read", "toolCallId": "c", "content": text}}]
        event = parse_pi_session(PiSessionInput(content=archive(records)))[0]
        registry = self.fixture.registry
        source = await registry.resolve(event)
        self.assertLessEqual(len(source.name), 256)
        self.assertEqual(await registry.read(source), event)
        observation = project_source_for_transport(registry, source)
        manifest = manifest_for_definition(PI_EVENT_SOURCE_DEFINITION)
        self.assertEqual(observation.definition_fingerprint, manifest.fingerprint)
        restored = SourceObservation.model_validate_json(observation.model_dump_json())
        evidence = registry.project(restored, TEXT_EVIDENCE_PROJECTION_KEY)
        self.assertEqual(evidence["content"], "toolResult: [tool_result read c] " + text)
        self.assertEqual(restored.payload["record"], records[0])

    async def test_official_public_fixture_full_record_projection(self):
        workload = Path(__file__).resolve().parents[1] / "competition/challenges/source-02-pi-history/workload/public"
        cases = json.loads((workload / "cases.json").read_text(encoding="utf-8"))["cases"]
        for case in cases:
            raw = (workload / case["file"]).read_bytes()
            # Git for Windows can check out JSONL as CRLF. Verify the published
            # LF bytes without rewriting the official fixture on disk.
            self.assertEqual(hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest(), case["sha256"])
            records = [json.loads(line) for line in raw.decode("utf-8-sig").split("\n") if line.strip()]
            by_id = {row["id"]: row for row in records[1:]}
            events = parse_pi_session(PiSessionInput(content=raw, leaf_id=case["leaf_id"]))
            self.assertTrue(events)
            for event in events:
                self.assertEqual(event.record, by_id[event.event_id])
                source = await self.fixture.registry.resolve(event)
                self.assertEqual(await self.fixture.registry.read(source), event)
                evidence = self.fixture.registry.project(source, TEXT_EVIDENCE_PROJECTION_KEY)
                self.assertEqual(evidence["content"], event.content)
                self.assertEqual(evidence["metadata"]["event_id"], event.event_id)

    async def test_branch_switch_append_restart(self):
        self.write()
        first = await self.run_leaf("left")
        self.assertEqual(len(first.items), 2)
        second = await self.run_leaf("right")
        self.assertEqual(len(second.items), 1)
        self.assertEqual(second.previous_checkpoint, first.committed_checkpoint)
        self.assertEqual(len(self.fixture.sources), 3)
        self.assertEqual((await self.run_leaf("left")).items, ())
        self.records.append(message("tail", "right", "tail"))
        self.write()
        self.assertEqual(len((await self.run_leaf("tail")).items), 1)

    async def test_corruption_and_off_branch_mutation_keep_progress(self):
        self.write()
        await self.run_leaf("left")
        await self.run_leaf("right")
        checkpoint = deepcopy(self.fixture.value)
        self.path.write_bytes(self.path.read_bytes() + b"\n{broken}")
        with self.assertRaises(ValueError):
            await self.run_leaf("right")
        self.assertEqual(self.fixture.value, checkpoint)
        self.records[1]["extra"] = "changed even though the selected leaf is right"
        self.write()
        with self.assertRaises(ValueError):
            await self.run_leaf("right")
        self.assertEqual(self.fixture.value, checkpoint)
        del self.records[1]["extra"]
        self.write()
        self.assertEqual((await self.run_leaf("right")).items, ())

    async def test_partial_submission_failure_and_retry(self):
        self.write()
        self.fixture.fail_id = "left"
        with self.assertRaises(OSError):
            await self.run_leaf("left")
        self.assertIsNone(self.fixture.value)
        self.assertEqual(len(self.fixture.sources), 1)
        self.fixture.fail_id = None
        result = await self.run_leaf("left")
        self.assertEqual(result.status, ConnectorRunStatus.COMPLETE)
        self.assertEqual(len(self.fixture.sources), 2)

    async def test_rejection_keeps_checkpoint(self):
        self.write()
        self.fixture.reject_id = "left"
        result = await self.run_leaf("left")
        self.assertEqual(result.status, ConnectorRunStatus.INCOMPLETE)
        self.assertIsNone(result.committed_checkpoint)
        self.assertIsNone(self.fixture.value)

    async def test_long_and_ambiguous_identifiers(self):
        sources = []
        for session_id, event_id in [("a:b", "c"), ("a", "b:c"), ("长" * 1000, "长" * 1000)]:
            event = parse_pi_session(PiSessionInput(content=archive([message(event_id, None, "x")], session_id)))[0]
            source = await self.fixture.registry.resolve(event)
            self.assertLessEqual(len(source.name), 256)
            sources.append(source.name)
        self.assertEqual(len(set(sources)), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
