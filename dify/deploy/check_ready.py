"""Wait for the actual local API, model and memory HTTP listeners to be ready."""
from __future__ import annotations

import argparse
import time
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, build_opener


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--dify-url", default="http://127.0.0.1:18080")
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    pending = {"Dify API": args.dify_url.rstrip("/") + "/console/api/setup",
               "local model": "http://127.0.0.1:8791/health",
               "PowerContext bridge": "http://127.0.0.1:8790/health"}
    opener = build_opener(ProxyHandler({}))
    deadline = time.monotonic() + args.timeout
    print("Waiting for Dify API, local model and memory HTTP services...", flush=True)
    while pending and time.monotonic() < deadline:
        for name, url in list(pending.items()):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                with opener.open(url, timeout=min(2, remaining)) as response:
                    if response.status == 200:
                        pending.pop(name)
                        print(name + ": ready", flush=True)
            except (HTTPError, URLError, TimeoutError, OSError):
                pass
        if pending:
            time.sleep(min(1, max(0, deadline - time.monotonic())))
    if pending:
        raise SystemExit("Not ready: " + ", ".join(pending) + ". Inspect service logs and retry.")
    print("All three HTTP services are ready. Run verify_dify.py to verify the workflow itself.", flush=True)


if __name__ == "__main__":
    main()
