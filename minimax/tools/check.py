"""Run checks and save command, exit code and exact source hashes for reviewers."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--integration", action="store_true")
parser.add_argument("--output", type=Path, default=root / ".runtime" / "module-check.json")
args = parser.parse_args()
env = os.environ.copy()
if args.integration:
    env["SDD_INTEGRATION"] = "1"
command = [sys.executable, "-m", "pytest", "-q"]
result = subprocess.run(command, cwd=root, env=env, text=True, capture_output=True, timeout=180)
files = sorted([*(root / "sdd_context").glob("*.py"), *(root / "tests").glob("*.py")])
record = {"command": ["python", "-m", "pytest", "-q"], "cwd": "minimax", "integration": args.integration,
          "exit_code": result.returncode, "stdout": result.stdout, "stderr": result.stderr,
          "code_versions_sha256": {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(result.stdout, end="")
print(result.stderr, end="", file=sys.stderr)
raise SystemExit(result.returncode)
