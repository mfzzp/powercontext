"""Verify delivered source, immutable tests, model patch and recovery evidence agree."""
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


response = read(root / "evidence/live/minimax-response.json")
edit = json.loads(response["content"])
assert response["model_invoked"] is True and response["id"] and response["model"] == "MiniMax-M3"
assert edit["path"] == "ticket_app.py"
assert edit["content"] == (root / "sample/after/ticket_app.py").read_text(encoding="utf-8")
for mode in ["live", "replay"]:
    evidence = root / "evidence" / mode
    assert sha(evidence / "before.py") == sha(root / "sample/before/ticket_app.py")
    assert sha(evidence / "after.py") == sha(root / "sample/after/ticket_app.py")
    for phase, kind, expected, count in [("before", "target", 1, 7), ("before", "regression", 0, 6),
                                         ("after", "target", 0, 7), ("after", "regression", 0, 6)]:
        record = read(evidence / f"{phase}-{kind}.json")
        assert record["exit_code"] == expected and f"Ran {count} tests" in record["stderr"]
        assert "ERROR:" not in record["stderr"]
        assert record["source_sha256"] == sha(evidence / f"{phase}.py")
        for name, value in record["test_sha256"].items():
            assert value == sha(root / "sample/tests" / name)
    first = read(evidence / "context-v1.json")
    updated = read(evidence / "context-v2.json")
    for context, version in [(first, 1), (updated, 2)]:
        spec = next(s for s in context["sources"] if s["path"] == "specs/status.md")
        assert spec["revision"] == version
        assert spec["content"] == (root / f"sample/specs/status_v{version}.md").read_text(encoding="utf-8")
        assert {s["path"] for s in context["sources"]} == set(context["task"]["required_sources"])
        assert len(context["query_record"]["results"]) == len(context["sources"])
        for source in context["sources"]:
            assert hashlib.sha256(source["content"].encode()).hexdigest() == source["sha256"]
    resumed = read(evidence / "context-resumed.json")
    finished = read(evidence / "context-finished.json")
    summary = read(evidence / "summary.json")
    assert read(evidence / "state-empty.json")["state"] is None
    assert resumed["state_reused"] and resumed["next_action"] == "implement"
    assert resumed["progress"]["revision"] == 1
    assert finished["state_reused"] and finished["next_action"] == "complete"
    assert finished["progress"]["revision"] == 2
    assert summary["parent_pid"] != resumed["process_id"]
    assert summary["parent_pid"] != finished["process_id"]
    assert summary["model_invoked"] == (mode == "live")
request = read(root / "evidence/live/minimax-request.json")
assert json.loads(request["body"]["messages"][1]["content"]) == read(root / "evidence/live/context-resumed.json")
checks = read(root / "evidence/module-check.json")
assert checks["exit_code"] == 0 and checks["integration"]
for path, value in checks["code_versions_sha256"].items():
    assert value == sha(root / path), path
print("Evidence verified: real model patch, fixed tests, current sources, SQL records, process restart and source hashes agree")
