import difflib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .config import ROOT, digest, read_json, write_json
from .model import generate, parse_edit
from .state import state
from .store import DocumentStore


def context(project, task, state_path):
    store = DocumentStore()
    try:
        sources, query_record = store.search(project, task["query"])
    finally:
        store.close()
    progress = state(state_path, project, task["task_id"])
    current_versions = {s["path"]: s["sha256"] for s in sources}
    content = progress["content"] if progress else {"completed": [], "pending": ["implement", "validate"]}
    stale = bool(progress) and content.get("source_versions") != current_versions
    next_action = "revalidate_changed_sources" if stale else (
        content["pending"][0] if content["pending"] else "complete")
    return {"task": task, "sources": sources, "progress": progress, "next_action": next_action,
            "state_reused": progress is not None, "source_versions_changed": stale,
            "process_id": os.getpid(), "query_record": query_record}


def test_snapshot(project, pattern, log):
    command = [sys.executable, "-I", "-m", "unittest", "discover", "-s", "tests", "-t", ".", "-p", pattern, "-v"]
    # unittest discovers from cwd, while -I avoids external PYTHONPATH/site customization.
    env = {k: v for k, v in os.environ.items() if not any(s in k.upper() for s in ("KEY", "TOKEN", "PASSWORD", "SECRET"))}
    result = subprocess.run(command, cwd=project, env=env, text=True, capture_output=True, timeout=40)
    record = {"command": ["python", *command[1:]], "cwd": "project", "exit_code": result.returncode,
              "source_sha256": digest((project / "ticket_app.py").read_bytes()),
              "test_sha256": {p.name: digest(p.read_bytes()) for p in sorted((project / "tests").glob("test_*.py"))},
              "stdout": result.stdout, "stderr": result.stderr}
    write_json(log, record)
    return record


def restarted_context(project_id, task_path, state_path, output):
    command = [sys.executable, "-m", "sdd_context", "context", "--project", project_id,
               "--task", str(task_path), "--state", str(state_path), "--output", str(output)]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=90)
    if result.returncode:
        raise RuntimeError("context subprocess failed: " + result.stderr[-1000:])
    child = read_json(output)
    if child["process_id"] == os.getpid():
        raise AssertionError("restart must use a different process")
    return child


def demo(run_name=None, replay=None):
    started_at = datetime.now(timezone.utc).isoformat()
    run_id = run_name or time.strftime("run-%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    if not run_id or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in run_id) or len(run_id) > 90:
        raise ValueError("run name must be 1..90 letters, digits, hyphens or underscores")
    run = ROOT / ".runtime" / run_id
    if run.exists():
        raise ValueError("run already exists; use a new --run name to start from empty state")
    project = run / "project"
    evidence = run / "evidence"
    evidence.mkdir(parents=True)
    project.mkdir()
    sample = ROOT / "sample"
    shutil.copytree(sample / "tests", project / "tests")
    (project / "tests" / "__init__.py").touch()
    shutil.copy2(sample / "before" / "ticket_app.py", project / "ticket_app.py")
    (project / "specs").mkdir()
    shutil.copy2(sample / "specs" / "status_v1.md", project / "specs" / "status.md")
    shutil.copy2(sample / "specs" / "billing.md", project / "specs" / "billing.md")
    task = read_json(sample / "task.json")
    state_path = run / "progress.sqlite"
    if state(state_path, run_id, task["task_id"]) is not None:
        raise AssertionError("demo must begin with empty PowerContext state")
    write_json(evidence / "state-empty.json", {"state": None, "process_id": os.getpid()})
    before_target = test_snapshot(project, "test_status.py", evidence / "before-target.json")
    before_regression = test_snapshot(project, "test_regression.py", evidence / "before-regression.json")
    if before_target["exit_code"] != 1 or "AssertionError" not in before_target["stderr"] or "ERROR:" in before_target["stderr"] or before_regression["exit_code"] != 0:
        raise AssertionError("before tests must fail for target assertions only, while regression passes")
    print("Before: target behavior fails; regression passes", flush=True)
    store = DocumentStore()
    try:
        with store.connection.cursor() as cursor:
            cursor.execute("SELECT VERSION() AS version")
            database_version = cursor.fetchone()["version"]
        write_json(evidence / "ingest-v1.json", store.ingest(run_id, project, read_json(sample / "manifest.json")))
        old = context(run_id, task, state_path)
        write_json(evidence / "context-v1.json", old)
        shutil.copy2(sample / "specs" / "status_v2.md", project / "specs" / "status.md")
        write_json(evidence / "ingest-v2.json", store.ingest(run_id, project, read_json(sample / "manifest.json")))
    finally:
        store.close()
    updated = context(run_id, task, state_path)
    by_path = {s["path"]: s for s in updated["sources"]}
    if not set(task["required_sources"]) <= by_path.keys():
        raise AssertionError("retrieval omitted required sample sources")
    if by_path["specs/status.md"]["revision"] != 2 or "strip().casefold()" not in by_path["specs/status.md"]["content"]:
        raise AssertionError("retrieval must use reimported specification v2")
    write_json(evidence / "context-v2.json", updated)
    progress = {"completed": ["import_sources", "verify_before_tests", "refresh_specification"],
                "pending": ["implement", "validate"],
                "validation": {"before_target_exit": 1, "before_regression_exit": 0},
                "source_versions": {s["path"]: s["sha256"] for s in updated["sources"]}}
    write_json(evidence / "state-write-1.json", state(state_path, run_id, task["task_id"], progress))
    resumed = restarted_context(run_id, sample / "task.json", state_path, evidence / "context-resumed.json")
    if resumed["next_action"] != "implement" or not resumed["state_reused"]:
        raise AssertionError("restarted process must reuse saved progress")
    print("seekdb context updated; PowerContext state reused in a new process", flush=True)
    if replay:
        recorded = read_json(replay)
        edit = parse_edit(recorded["content"])
        write_json(evidence / "replay.json", {"model_invoked": False, "source_sha256": digest(Path(replay).read_bytes()), "original_response_id": recorded.get("id")})
        write_json(evidence / "recorded-response.json", recorded)
    else:
        edit = generate(resumed, evidence)
    before_code = (project / "ticket_app.py").read_text(encoding="utf-8")
    (project / "ticket_app.py").write_text(edit["content"], encoding="utf-8", newline="\n")
    # Invalidate bytecode after a same-size, same-second edit.
    cache = project / "__pycache__"
    if cache.exists():
        for pyc in cache.glob("ticket_app.*.pyc"):
            pyc.unlink()
    after_target = test_snapshot(project, "test_status.py", evidence / "after-target.json")
    after_regression = test_snapshot(project, "test_regression.py", evidence / "after-regression.json")
    (evidence / "before.py").write_text(before_code, encoding="utf-8", newline="\n")
    (evidence / "after.py").write_text(edit["content"], encoding="utf-8", newline="\n")
    (evidence / "change.diff").write_text("".join(difflib.unified_diff(before_code.splitlines(True), edit["content"].splitlines(True), fromfile="before/ticket_app.py", tofile="after/ticket_app.py")), encoding="utf-8", newline="\n")
    if before_target["test_sha256"] != after_target["test_sha256"] or before_regression["test_sha256"] != after_regression["test_sha256"]:
        raise AssertionError("tests changed during model edit")
    if after_target["exit_code"] or after_regression["exit_code"]:
        raise AssertionError("model edit did not pass unchanged tests; inspect evidence")
    # Reimport actual final source and persist the final context version with the test results.
    store = DocumentStore()
    try:
        write_json(evidence / "ingest-final.json", store.ingest(run_id, project, read_json(sample / "manifest.json")))
    finally:
        store.close()
    final_sources = context(run_id, task, state_path)["sources"]
    progress["source_versions"] = {s["path"]: s["sha256"] for s in final_sources}
    progress["completed"] += ["implement", "validate"]
    progress["pending"] = []
    progress["validation"].update(after_target_exit=0, after_regression_exit=0, code_sha256=after_target["source_sha256"])
    write_json(evidence / "state-write-2.json", state(state_path, run_id, task["task_id"], progress))
    finished = restarted_context(run_id, sample / "task.json", state_path, evidence / "context-finished.json")
    if finished["next_action"] != "complete" or finished["progress"]["revision"] != 2:
        raise AssertionError("completed progress was not recovered")
    summary = {"run_id": run_id, "started_at_utc": started_at, "model_invoked": replay is None, "parent_pid": os.getpid(),
               "resume_pid": resumed["process_id"], "final_resume_pid": finished["process_id"],
               "database_version": database_version,
               "dependencies": {p: importlib.metadata.version(p) for p in ["powercontext", "PyMySQL", "httpx", "pytest"]},
               "python": sys.version, "target_before_exit": 1, "regression_before_exit": 0,
               "target_after_exit": 0, "regression_after_exit": 0,
               "tests_unchanged": True, "spec_revision": 2, "powercontext_final_revision": 2,
               "code_sha256": after_target["source_sha256"]}
    write_json(evidence / "summary.json", summary)
    print(json.dumps({"result": "passed", "evidence": str(evidence), "summary": summary}, ensure_ascii=False), flush=True)
    return evidence
