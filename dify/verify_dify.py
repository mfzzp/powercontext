"""Execute the real published Dify workflow and retain actual (not simulated) evidence."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import uuid

import httpx

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--player", required=True, help="本次验证专用新玩家码")
    parser.add_argument("--phase", required=True, choices=("first", "return", "repair"))
    args = parser.parse_args()
    key = os.environ.get("DIFY_APP_KEY", "")
    if not key:
        raise SystemExit("请设置 DIFY_APP_KEY（工作流 API 密钥，不是模型密钥）")
    base = os.environ.get("DIFY_API_URL", "http://localhost/v1").rstrip("/")
    actions = {"first": "我把你的咖啡价签换成‘学一声猫叫’，并自称猫叫大王。",
               "return": "我又回来了。你还记得我上次在这里干了什么吗？",
               "repair": "我把咖啡价签恢复原样，认真道歉，再帮你整理货架。"}
    turn = args.phase + "-" + uuid.uuid4().hex[:12]
    response = httpx.post(base + "/workflows/run", headers={"Authorization": "Bearer " + key},
                          json={"inputs": {"player_id": args.player, "turn_id": turn, "action": actions[args.phase]},
                                "response_mode": "blocking", "user": "npc-demo-" + args.player}, timeout=240)
    response.raise_for_status()
    value = response.json()
    record = {"executed_at": datetime.now(timezone.utc).isoformat(), "player_id": args.player,
              "phase": args.phase, "turn_id": turn, "action": actions[args.phase],
              "dsl_sha256": hashlib.sha256((ROOT / "grudge-shop.yml").read_bytes()).hexdigest(),
              "workflow_run_id": value.get("workflow_run_id"), "data": value.get("data")}
    folder = ROOT / "evidence"
    folder.mkdir(exist_ok=True)
    target = folder / ("live-" + args.player + "-" + turn + ".json")
    target.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    data = value.get("data", {})
    if data.get("status") != "succeeded":
        raise SystemExit(f"Dify 运行失败，实际返回已保存：{target}")
    outputs = data.get("outputs", {})
    if not outputs.get("reply") or not outputs.get("memory_receipt"):
        raise SystemExit(f"缺少回复或记忆凭据：{target}")
    print(outputs["reply"])
    print("\n记忆凭据：", outputs["memory_receipt"])
    print("证据文件：", target)


if __name__ == "__main__":
    main()
