"""Fetch public, pinned CPU model assets; model inference happens locally."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import time
from urllib.request import Request, urlopen


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download(asset: dict, folder: Path, workers: int = 8):
    target = folder / asset["filename"]
    folder.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.stat().st_size == asset["size"] and sha256(target) == asset["sha256"]:
        print("Verified cached asset: " + target.name, flush=True)
        return
    parts = folder / (target.name + ".parts")
    parts.mkdir(exist_ok=True)
    chunk_size = 8 * 1024 * 1024
    ranges = [(start, min(chunk_size, asset["size"] - start)) for start in range(0, asset["size"], chunk_size)]

    def fetch(item):
        start, size = item
        part = parts / f"{start:012d}"
        for attempt in range(8):
            offset = part.stat().st_size if part.exists() else 0
            if offset == size:
                return
            if offset > size:
                raise RuntimeError("Unexpected part size: " + str(part))
            headers = {"User-Agent": "Dify-Grudge-Shop/1.0", "Accept-Encoding": "identity",
                       "Range": f"bytes={start + offset}-{start + size - 1}"}
            if asset.get("github_asset"):
                headers.update({"Accept": "application/octet-stream", "X-GitHub-Api-Version": "2026-03-10"})
            try:
                with urlopen(Request(asset["url"], headers=headers), timeout=60) as response:
                    if response.status != 206 or not response.headers.get("Content-Range", "").startswith(f"bytes {start + offset}-"):
                        raise RuntimeError("Server did not honor the asset byte range")
                    with part.open("ab") as output:
                        while offset < size:
                            data = response.read(min(64 * 1024, size - offset))
                            if not data:
                                break
                            output.write(data)
                            offset += len(data)
                if offset == size:
                    return
            except Exception as error:
                if attempt == 7:
                    raise RuntimeError(f"Asset {target.name}, range {start}: {error}") from error
            time.sleep(2)
        raise RuntimeError("Incomplete asset range")

    print(f"Downloading {target.name}: {asset['size']} bytes", flush=True)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for count, result in enumerate(as_completed([pool.submit(fetch, item) for item in ranges]), 1):
            result.result()
            if count % 8 == 0 or count == len(ranges):
                print(f"  completed {count}/{len(ranges)} ranges", flush=True)
    with target.open("wb") as output:
        for start, size in ranges:
            data = (parts / f"{start:012d}").read_bytes()
            if len(data) != size:
                raise RuntimeError("Asset part length mismatch")
            output.write(data)
    if sha256(target) != asset["sha256"]:
        raise RuntimeError("Official asset SHA256 mismatch: " + target.name)
    print("Official SHA256 verified: " + target.name, flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset", choices=("all", "llama", "model"), default="all")
    parser.add_argument("--directory", type=Path, default=Path.home() / ".local/share/dify-grudge-shop/assets")
    parser.add_argument("--workers", type=int, choices=range(1, 17), default=8)
    args = parser.parse_args()
    assets = json.loads(Path(__file__).with_name("assets.json").read_text(encoding="utf-8"))
    for name, asset in assets.items():
        if args.asset in ("all", name):
            download(asset, args.directory, args.workers)


if __name__ == "__main__":
    main()
