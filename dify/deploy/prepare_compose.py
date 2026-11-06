"""Configure a dedicated, local Dify 1.13.0 checkout; never print credentials."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import secrets
import subprocess


UPSTREAM_COMMIT = "c730fec1e422e907fed668291b2ea18c4c243644"


def private_write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, default=Path.home() / ".local/share/dify-grudge-shop")
    args = parser.parse_args()
    root = args.home.resolve()
    docker = root / "dify-upstream/docker"
    if not (docker / ".env.example").exists():
        raise SystemExit("Clone Dify tag 1.13.0 into " + str(root / "dify-upstream") + " first")
    head = subprocess.check_output(["git", "-C", str(docker.parent), "rev-parse", "HEAD"], text=True).strip()
    if head != UPSTREAM_COMMIT:
        raise SystemExit("Expected Dify 1.13.0 commit " + UPSTREAM_COMMIT + "; found " + head)
    credentials_path = root / "credentials.json"
    if credentials_path.exists():
        credentials = json.loads(credentials_path.read_text(encoding="utf-8"))
    else:
        credentials = {name: secrets.token_hex(24) for name in (
            "secret_key", "db_password", "redis_password", "sandbox_key", "plugin_key",
            "plugin_inner_key", "qdrant_key", "npc_token", "model_token")}
        credentials.update(admin_email="reviewer@example.com", admin_password="Dify1" + secrets.token_hex(12))
        private_write(credentials_path, json.dumps(credentials, indent=2) + "\n")
    values = {
        "SECRET_KEY": credentials["secret_key"], "DB_PASSWORD": credentials["db_password"],
        "REDIS_PASSWORD": credentials["redis_password"], "SANDBOX_API_KEY": credentials["sandbox_key"],
        "CELERY_BROKER_URL": "redis://:" + credentials["redis_password"] + "@redis:6379/1",
        "CODE_EXECUTION_API_KEY": credentials["sandbox_key"], "PLUGIN_DAEMON_KEY": credentials["plugin_key"],
        "PLUGIN_DIFY_INNER_API_KEY": credentials["plugin_inner_key"], "QDRANT_API_KEY": credentials["qdrant_key"],
        "VECTOR_STORE": "qdrant", "DB_TYPE": "postgresql", "COMPOSE_PROFILES": "qdrant,postgresql",
        "EXPOSE_NGINX_PORT": "18080", "EXPOSE_NGINX_SSL_PORT": "18443",
        "EXPOSE_PLUGIN_DEBUGGING_PORT": "15003", "PM2_INSTANCES": "1",
        "SERVER_WORKER_AMOUNT": "1", "CELERY_WORKER_AMOUNT": "1", "CELERY_AUTO_SCALE": "false",
        "TEXT_GENERATION_TIMEOUT_MS": "300000", "PLUGIN_MAX_EXECUTION_TIMEOUT": "300",
        "NEXT_TELEMETRY_DISABLED": "1", "PLUGIN_PYTHON_ENV_INIT_TIMEOUT": "600",
    }
    env = (docker / ".env.example").read_text(encoding="utf-8")
    for key, value in values.items():
        pattern = rf"^{re.escape(key)}=.*$"
        if re.search(pattern, env, flags=re.MULTILINE):
            env = re.sub(pattern, key + "=" + value, env, flags=re.MULTILINE)
        else:
            env += "\n" + key + "=" + value + "\n"
    private_write(docker / ".env", env)
    override = "services:\n"
    for service in ("api", "worker", "worker_beat", "plugin_daemon", "ssrf_proxy"):
        override += f'  {service}:\n    extra_hosts:\n      - "host.docker.internal:host-gateway"\n'
        if service == "ssrf_proxy":
            override += '    volumes:\n      - "./grudge-squid.conf.template:/etc/squid/squid.conf.template"\n'
        if service == "plugin_daemon":
            override += '    ports: !override\n      - "127.0.0.1:15003:5003"\n'
    override += '  nginx:\n    ports: !override\n      - "127.0.0.1:18080:80"\n      - "127.0.0.1:18443:443"\n'
    squid = (docker / "ssrf_proxy/squid.conf.template").read_text(encoding="utf-8")
    rule = ("acl grudge_bridge dstdomain host.docker.internal\n"
            "acl grudge_bridge_port port 8790\n"
            "http_access allow grudge_bridge grudge_bridge_port\n")
    squid = squid.replace("http_access deny all", rule + "http_access deny all", 1)
    (docker / "grudge-squid.conf.template").write_text(squid, encoding="utf-8")
    (docker / "docker-compose.override.yaml").write_text(override, encoding="utf-8")
    print("Configured local Dify at http://127.0.0.1:18080; credentials kept private in " + str(credentials_path))


if __name__ == "__main__":
    main()
