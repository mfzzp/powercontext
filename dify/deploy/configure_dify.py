"""Initialize a dedicated local Dify, install the official model plugin and import DSL.

Credentials stay in the local Linux deployment directory, never in the exported DSL.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import sys
import time
from urllib.request import Request, urlopen

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from build_dsl import MODEL_NAME, MODEL_PROVIDER, PLUGIN_IDENTIFIER

PLUGIN_SHA256 = "a3c6524f183083e0f6f57ac50f201700fd019e786bd1971249ef086adcb70008"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, default=Path.home() / ".local/share/dify-grudge-shop")
    parser.add_argument("--url", default="http://127.0.0.1:18080")
    parser.add_argument("--app-id", help="更新已有应用；首次部署不需要")
    parser.add_argument("--plugin-package", type=Path, default=ROOT / "data/openai_api_compatible-0.0.73.difypkg")
    args = parser.parse_args()
    credentials_path = args.home / "credentials.json"
    credentials = json.loads(credentials_path.read_text(encoding="utf-8"))
    if args.app_id:
        credentials["app_id"] = args.app_id
    client = httpx.Client(base_url=args.url.rstrip("/") + "/console/api/", timeout=90, trust_env=False)
    for attempt in range(120):
        try:
            ready = client.get("setup", timeout=3)
            if ready.status_code == 200:
                break
        except httpx.RequestError:
            pass
        if attempt % 15 == 0:
            print("Waiting for local Dify API and initial database migrations", flush=True)
        time.sleep(2)
    else:
        raise RuntimeError("Local Dify API is not ready; inspect docker compose logs api")

    def call(method, route, **kwargs):
        csrf = client.cookies.get("csrf_token")
        if csrf:
            client.headers["X-CSRF-Token"] = csrf
        response = client.request(method, route, **kwargs)
        if response.status_code >= 400:
            try:
                detail = response.json()
                message = str(detail.get("message", detail.get("detail", detail.get("code", ""))))
            except ValueError:
                message = "Non-JSON error; inspect local Dify logs"
            for value in credentials.values():
                if isinstance(value, str) and value:
                    message = message.replace(value, "[redacted]")
            raise RuntimeError(f"Dify {method} {route}: HTTP {response.status_code}: {message[:1200]}")
        return response.json() if response.content else {}

    if call("GET", "setup")["step"] == "not_started":
        call("POST", "setup", json={"email": credentials["admin_email"], "name": "记仇便利店",
                                     "password": credentials["admin_password"], "language": "zh-Hans"})
        print("Created local administrator", flush=True)
    call("POST", "login", json={"email": credentials["admin_email"],
                                  "password": base64.b64encode(credentials["admin_password"].encode()).decode(),
                                  "remember_me": True})

    def installed():
        plugins = call("GET", "workspaces/current/plugin/list", params={"page": 1, "page_size": 256})
        return "langgenius/openai_api_compatible" in json.dumps(plugins)

    if not installed():
        package = args.plugin_package
        if not package.exists():
            package.parent.mkdir(parents=True, exist_ok=True)
            url = "https://marketplace.dify.ai/api/v1/plugins/download?unique_identifier=" + PLUGIN_IDENTIFIER
            with urlopen(Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=90) as response:
                package.write_bytes(response.read())
        if hashlib.sha256(package.read_bytes()).hexdigest() != PLUGIN_SHA256:
            raise RuntimeError("Official plugin package checksum mismatch")
        with package.open("rb") as stream:
            uploaded = call("POST", "workspaces/current/plugin/upload/pkg", files={"pkg": (package.name, stream)})
        identifier = uploaded.get("unique_identifier") or uploaded.get("plugin_unique_identifier")
        if not identifier:
            raise RuntimeError("Plugin upload did not return identifier; response keys: " + str(list(uploaded)))
        call("POST", "workspaces/current/plugin/install/pkg", json={"plugin_unique_identifiers": [identifier]})
        print("Installing official OpenAI-compatible model plugin", flush=True)
        for _ in range(120):
            if installed():
                break
            time.sleep(5)
        else:
            raise RuntimeError("Plugin installation timed out; inspect local plugin_daemon logs")
    print("Official model plugin available", flush=True)
    route = "workspaces/current/model-providers/" + MODEL_PROVIDER + "/models"
    model_credentials = {"api_key": credentials["model_token"], "endpoint_url": "http://host.docker.internal:8791/v1",
                         "endpoint_model_name": MODEL_NAME, "display_name": "本机 " + MODEL_NAME,
                         "mode": "chat", "context_size": "8192", "max_tokens_to_sample": "1400",
                         "agent_thought_support": "not_supported", "function_calling_type": "no_call",
                         "stream_function_calling": "not_supported", "vision_support": "no_support",
                         "structured_output_support": "not_supported", "stream_include_usage": "enabled"}
    valid = call("POST", route + "/credentials/validate",
                 json={"model": MODEL_NAME, "model_type": "llm", "credentials": model_credentials})
    if valid.get("result") != "success":
        raise RuntimeError("Local model credential validation failed; inspect local plugin log")
    current = call("GET", route + "/credentials", params={"model": MODEL_NAME, "model_type": "llm"})
    if not current.get("current_credential_id"):
        call("POST", route + "/credentials", json={"model": MODEL_NAME, "model_type": "llm",
                                                    "credentials": model_credentials, "name": "local-qwen"})
        current = call("GET", route + "/credentials", params={"model": MODEL_NAME, "model_type": "llm"})
    call("POST", route, json={"model": MODEL_NAME, "model_type": "llm", "config_from": "customizable-model",
                               "credential_id": current["current_credential_id"]})
    print("Local model validated", flush=True)
    dsl = yaml.safe_load((ROOT / "grudge-shop.yml").read_text(encoding="utf-8"))
    for item in dsl["workflow"]["environment_variables"]:
        if item["name"] == "NPC_API_TOKEN":
            item["value"] = credentials["npc_token"]
    request = {"mode": "yaml-content", "yaml_content": yaml.safe_dump(dsl, allow_unicode=True), "name": dsl["app"]["name"]}
    if credentials.get("app_id"):
        request["app_id"] = credentials["app_id"]
    imported = call("POST", "apps/imports", json=request)
    if imported.get("status") == "pending":
        imported = call("POST", "apps/imports/" + imported["id"] + "/confirm")
    app_id = imported.get("app_id")
    if not app_id:
        raise RuntimeError("DSL import did not return app_id")
    credentials["app_id"] = app_id
    credentials_path.write_text(json.dumps(credentials, indent=2) + "\n", encoding="utf-8")
    credentials_path.chmod(0o600)
    call("POST", f"apps/{app_id}/workflows/publish", json={"marked_name": "Qwen+PowerContext"})
    keys = call("GET", f"apps/{app_id}/api-keys").get("data", [])
    key = keys[0] if keys else call("POST", f"apps/{app_id}/api-keys")
    credentials["app_key"] = key["token"]
    credentials_path.write_text(json.dumps(credentials, indent=2) + "\n", encoding="utf-8")
    credentials_path.chmod(0o600)
    detail = call("GET", f"apps/{app_id}")
    site_token = detail.get("site", {}).get("code", "")
    public = {"dify_version": "1.13.0", "app_id": app_id, "provider": MODEL_PROVIDER, "model": MODEL_NAME,
              "plugin": PLUGIN_IDENTIFIER, "studio_url": args.url + f"/app/{app_id}/workflow",
              "webapp_url": args.url + "/workflow/" + site_token,
              "dsl_sha256": hashlib.sha256((ROOT / "grudge-shop.yml").read_bytes()).hexdigest()}
    (args.home / "deployment.json").write_text(json.dumps(public, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(public, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
