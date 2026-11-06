"""Export actual Dify node records and matching PowerContext audit, with secrets removed."""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import httpx
import yaml


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--folder", required=True, type=Path, help="Directory containing actual phase JSON results")
    parser.add_argument("--home", type=Path, default=Path.home() / ".local/share/dify-grudge-shop")
    parser.add_argument("--url", default="http://127.0.0.1:18080")
    args = parser.parse_args()
    credentials = json.loads((args.home / "credentials.json").read_text(encoding="utf-8"))
    secrets = [v for k, v in credentials.items() if isinstance(v, str) and
               ("password" in k or "token" in k or "key" in k)]

    def clean(value):
        if isinstance(value, str):
            for secret in secrets:
                if secret:
                    value = value.replace(secret, "[REDACTED]")
            return value
        if isinstance(value, list):
            return [clean(item) for item in value]
        if isinstance(value, dict):
            return {key: "[REDACTED]" if key.lower() in ("authorization", "api_key", "password", "cookie", "credentials")
                    else clean(item) for key, item in value.items()}
        return value

    def save(name, value):
        (args.folder / name).write_text(json.dumps(clean(value), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    with httpx.Client(base_url=args.url.rstrip("/") + "/console/api/", trust_env=False, timeout=60) as client:
        response = client.post("login", json={"email": credentials["admin_email"],
                               "password": base64.b64encode(credentials["admin_password"].encode()).decode(),
                               "remember_me": True})
        response.raise_for_status()
        client.headers["X-CSRF-Token"] = client.cookies["csrf_token"]
        app_id = credentials["app_id"]
        records = []
        for path in sorted(args.folder.glob("*.json")):
            value = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(value, dict) and value.get("workflow_run_id") and value.get("phase"):
                records.append(value)
        if not records:
            raise SystemExit("No actual Workflow API result files found")
        summary = []
        for record in records:
            run_id, phase = record["workflow_run_id"], record["phase"]
            response = client.get(f"apps/{app_id}/workflow-runs/{run_id}/node-executions")
            response.raise_for_status()
            nodes = response.json()
            save(phase + "-nodes.json", nodes)
            values = nodes["data"]
            assert len(values) == 9, (phase, "Expected nine actual node executions")
            assert all(item["status"] == "succeeded" for item in values), (phase, "Node failed")
            read = next(item for item in values if item["node_id"] == "read")
            prepared = json.loads(read["outputs"]["body"])
            summary.append({"phase": phase, "workflow_run_id": run_id, "player_id": record["player_id"],
                            "remembered_turns": prepared["remembered_turns"],
                            "history": json.loads(prepared["history"]),
                            "saved_turns": record["data"]["outputs"]["saved_turns"], "node_count": len(values)})
        response = client.get(f"apps/{app_id}/export", params={"include_secret": "false"})
        response.raise_for_status()
        exported = yaml.safe_load(response.json()["data"])
        for item in exported.get("workflow", {}).get("environment_variables", []):
            if item.get("value_type") == "secret":
                item["value"] = ""
        yaml_content = yaml.safe_dump(clean(exported), allow_unicode=True, sort_keys=False)
        assert all(secret not in yaml_content for secret in secrets if secret)
        (args.folder / "dify-export.yml").write_text(yaml_content, encoding="utf-8")
    players = {record["player_id"] for record in records}
    audit = [json.loads(line) for line in (args.home / "memory/powercontext-audit.jsonl").read_text(encoding="utf-8").splitlines()]
    audit = [item for item in audit if item.get("player_id") in players]
    (args.folder / "powercontext-audit.jsonl").write_text("".join(json.dumps(clean(item), ensure_ascii=False) + "\n"
                                                                for item in audit), encoding="utf-8")
    for record in summary:
        expected = {"first": 0, "return": 1, "repair": 2, "isolation": 0}[record["phase"]]
        assert record["remembered_turns"] == expected, (record["phase"], record["remembered_turns"], expected)
        assert record["saved_turns"] == expected + 1
    save("verification-summary.json", {"exported_at": datetime.now(timezone.utc).isoformat(), "app_id": app_id,
                                      "runs": summary, "audit_records": len(audit),
                                      "exported_dsl_sha256": hashlib.sha256(yaml_content.encode()).hexdigest()})
    print("Exported actual node records, DSL, and PowerContext audit; all recorded memory counts match.")
    print(json.dumps([{k: v for k, v in item.items() if k != "history"} for item in summary], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
