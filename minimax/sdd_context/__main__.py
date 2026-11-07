import argparse
import sys
import time
from pathlib import Path

from .config import ROOT, load_env, read_json, write_json


def main():
    parser = argparse.ArgumentParser(description="SDD context: seekdb retrieval, MiniMax code edit, PowerContext progress")
    commands = parser.add_subparsers(dest="command", required=True)
    doctor = commands.add_parser("doctor", help="Wait for seekdb and initialize the context table")
    doctor.add_argument("--wait", type=int, default=0)
    run = commands.add_parser("demo", help="Run complete live example or replay a recorded MiniMax edit")
    run.add_argument("--run")
    run.add_argument("--replay", type=Path)
    ingest = commands.add_parser("ingest", help="Import a manifest snapshot into seekdb")
    ingest.add_argument("--project", required=True)
    ingest.add_argument("--root", type=Path, required=True)
    ingest.add_argument("--manifest", type=Path, required=True)
    ingest.add_argument("--output", type=Path, required=True)
    ctx = commands.add_parser("context", help="Query sources and reuse persisted PowerContext state")
    ctx.add_argument("--project", required=True)
    ctx.add_argument("--task", type=Path, default=ROOT / "sample" / "task.json")
    ctx.add_argument("--state", type=Path, required=True)
    ctx.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    load_env()
    if args.command == "doctor":
        from .store import DocumentStore
        deadline = time.monotonic() + max(0, args.wait)
        while True:
            try:
                store = DocumentStore()
                try:
                    with store.connection.cursor() as cursor:
                        cursor.execute("SELECT VERSION() AS version")
                        print(cursor.fetchone()["version"])
                finally:
                    store.close()
                break
            except Exception:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(2)
    elif args.command == "demo":
        from .workflow import demo
        demo(args.run, args.replay)
    elif args.command == "context":
        from .workflow import context
        write_json(args.output, context(args.project, read_json(args.task), args.state))
    else:
        from .store import DocumentStore
        store = DocumentStore()
        try:
            write_json(args.output, store.ingest(args.project, args.root, read_json(args.manifest)))
        finally:
            store.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Error messages are deliberately bounded; never dump environment, HTTP headers or SQL connections.
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(1)
