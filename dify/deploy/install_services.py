"""Root-only: install dedicated local systemd services (no autostart on boot)."""
import json
import os
from pathlib import Path
import pwd
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    if sys.platform != "linux" or os.geteuid() != 0:
        raise SystemExit("Run inside Linux/WSL as root: install_services.py USER PYTHON")
    if len(sys.argv) != 3:
        raise SystemExit("Usage: install_services.py USER /absolute/path/to/venv/bin/python")
    user, python = sys.argv[1:]
    account = pwd.getpwnam(user)
    root = Path(account.pw_dir) / ".local/share/dify-grudge-shop"
    credentials = json.loads((root / "credentials.json").read_text(encoding="utf-8"))
    model = json.loads((ROOT / "deploy/assets.json").read_text(encoding="utf-8"))["model"]
    if not (root / "assets" / model["filename"]).exists():
        raise SystemExit("Download and verify the configured model first")
    for filename, content in (("bridge.env", "NPC_API_TOKEN=" + credentials["npc_token"] + "\n"),
                              ("model.key", credentials["model_token"] + "\n")):
        target = root / filename
        target.write_text(content, encoding="utf-8")
        target.chmod(0o600)
        os.chown(target, account.pw_uid, account.pw_gid)
    commands = {
        "model": f'{root}/llama/llama-b12144/llama-server --model {root}/assets/{model["filename"]} '
                 f'--alias {model["alias"]} --host 0.0.0.0 --port 8791 --ctx-size 8192 --parallel 1 '
                 f'--threads 8 --api-key-file {root}/model.key --chat-template-kwargs \'{{"enable_thinking":false}}\'',
        "memory": f'{python} {ROOT}/npc_service.py --host 0.0.0.0 --port 8790 --data-dir {root}/memory',
    }
    for name, command in commands.items():
        unit = ("[Unit]\nDescription=Dify grudge shop " + name + "\nAfter=network.target\n"
                "[Service]\nType=simple\nUser=" + user + "\nWorkingDirectory=" + str(ROOT) + "\n"
                "EnvironmentFile=" + str(root / "bridge.env") + "\nEnvironment=PYTHONUNBUFFERED=1\n"
                "ExecStart=" + command + "\nRestart=on-failure\nRestartSec=3\n")
        Path("/etc/systemd/system/dify-grudge-" + name + ".service").write_text(unit, encoding="utf-8")
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "start", "dify-grudge-model", "dify-grudge-memory"], check=True)
    subprocess.run(["systemctl", "is-active", "dify-grudge-model", "dify-grudge-memory"], check=True)


if __name__ == "__main__":
    main()
