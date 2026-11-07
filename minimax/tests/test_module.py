import json
import os
import subprocess
import sys
import uuid

import pytest

from sdd_context.config import ROOT, digest, load_env, safe_source
from sdd_context.model import parse_edit
from sdd_context.state import state


def test_source_cannot_escape(tmp_path):
    with pytest.raises(ValueError):
        safe_source(tmp_path, "../credentials")
    assert safe_source(tmp_path, "src/main.py") == tmp_path / "src" / "main.py"


def test_environment_does_not_overwrite(monkeypatch, tmp_path):
    path = tmp_path / ".env"
    path.write_text("SDD_TEST_CONFIG=file\nSDD_TEST_NEW='hello'\n", encoding="utf-8")
    monkeypatch.setenv("SDD_TEST_CONFIG", "environment")
    monkeypatch.delenv("SDD_TEST_NEW", raising=False)
    load_env(path)
    assert os.environ["SDD_TEST_CONFIG"] == "environment"
    assert os.environ["SDD_TEST_NEW"] == "hello"


def test_edit_parser_accepts_function():
    edit = {"path": "ticket_app.py", "content": "def filter_tickets(tickets, *, status=None, limit=None):\n    return list(tickets)\n"}
    assert parse_edit("```json\n" + json.dumps(edit) + "\n```") == edit


@pytest.mark.parametrize("edit", [
    {"path": "../ticket_app.py", "content": "pass"},
    {"path": "tests/test_status.py", "content": "pass"},
    {"path": "ticket_app.py", "content": "import os\n"},
    {"path": "ticket_app.py", "content": "def filter_tickets(t):\n    return open('secret').read()\n"},
    {"path": "ticket_app.py", "content": "def filter_tickets(t):\n    return t.__class__\n"},
    {"path": "ticket_app.py", "content": "def broken("},
    {"path": "ticket_app.py", "content": "def filter_tickets(t=open('secret')):\n    return t\n"},
    {"path": "ticket_app.py", "content": "def filter_tickets(t):\n    return t.pop()\n"},
])
def test_reject_unsupported_edit(edit):
    with pytest.raises((ValueError, SyntaxError)):
        parse_edit(json.dumps(edit))


def test_real_powercontext_restart(tmp_path):
    path = tmp_path / "state.sqlite"
    assert state(path, "test", "task") is None
    progress = {"completed": ["ingest"], "pending": ["implement"], "validation": {"before": 1}, "source_versions": {"spec": "abc"}}
    first = state(path, "test", "task", progress)
    assert first["revision"] == 1
    code = "from sdd_context.state import state; import json,sys; print(json.dumps(state(sys.argv[1],'test','task')))"
    child = subprocess.run([sys.executable, "-c", code, str(path)], cwd=ROOT, capture_output=True, text=True, timeout=60, check=True)
    assert json.loads(child.stdout)["content"] == first["content"]
    progress["pending"] = []
    assert state(path, "test", "task", progress)["revision"] == 2
    assert state(path, "different-project", "task") is None


@pytest.mark.skipif(os.environ.get("SDD_INTEGRATION") != "1", reason="set SDD_INTEGRATION=1 with a real seekdb instance")
def test_real_database_revision_and_isolation(tmp_path):
    from sdd_context.store import DocumentStore
    from sdd_context.workflow import context
    load_env()
    project = "unit-" + uuid.uuid4().hex
    source = tmp_path / "spec.txt"
    source.write_text("ticket filter version one", encoding="utf-8")
    manifest = [{"path": "spec.txt", "kind": "spec"}]
    store = DocumentStore()
    try:
        assert store.ingest(project, tmp_path, manifest)[0]["revision"] == 1
        assert store.ingest(project, tmp_path, manifest)[0]["revision"] == 1
        assert store.search(project + "-other", "ticket")[0] == []
        source.write_text("ticket filter version two trim casefold", encoding="utf-8")
        assert store.ingest(project, tmp_path, manifest)[0]["revision"] == 2
        rows, audit = store.search(project, "casefold")
        assert len(rows) == 1 and rows[0]["sha256"] == digest(source.read_text())
        assert rows[0]["content"] == "ticket filter version two trim casefold"
        assert "%s" in audit["sql"] and "casefold" not in audit["sql"]
        progress = {"completed": ["implement", "validate"], "pending": [],
                    "source_versions": {"spec.txt": rows[0]["sha256"]}}
        state_path = tmp_path / "state.sqlite"
        state(state_path, project, "task", progress)
        task = {"task_id": "task", "query": "ticket"}
        assert context(project, task, state_path)["next_action"] == "complete"
        source.write_text("ticket version three different requirement", encoding="utf-8")
        store.ingest(project, tmp_path, manifest)
        assert context(project, task, state_path)["next_action"] == "revalidate_changed_sources"
        assert store.search(project, "x' OR 1=1 --")[0] == []
        # Invalid reimport must not delete the last valid data.
        with pytest.raises(ValueError):
            store.ingest(project, tmp_path, [{"path": "../private", "kind": "spec"}])
        assert store.search(project, "three")[0]
        store.ingest(project, tmp_path, [])
        assert store.search(project, "ticket")[0] == []
    finally:
        with store.connection.cursor() as cursor:
            cursor.execute("DELETE FROM sdd_documents WHERE project_id=%s", (project,))
        store.connection.commit()
        store.close()
