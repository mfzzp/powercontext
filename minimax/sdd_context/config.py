import hashlib
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_env(path=None):
    path = Path(path or ROOT / ".env.local")
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, sep, value = line.partition("=")
            if sep and re.fullmatch(r"[A-Z][A-Z0-9_]*", key.strip()):
                os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def digest(data):
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def safe_source(root, relative):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or path == root or Path(relative).is_absolute():
        raise ValueError("source must stay inside project root")
    return path


def db_settings():
    name = os.environ.get("SEEKDB_DATABASE", "minimax_sdd")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", name):
        raise ValueError("invalid database identifier")
    return dict(host=os.environ.get("SEEKDB_HOST", "127.0.0.1"),
                port=int(os.environ.get("SEEKDB_PORT", "2970")),
                user=os.environ.get("SEEKDB_USER", "root"),
                password=os.environ.get("SEEKDB_PASSWORD", ""), database=name,
                charset="utf8mb4", connect_timeout=10, read_timeout=30, write_timeout=30)
