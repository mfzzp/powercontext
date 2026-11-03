"""Pure functions embedded into Dify Python Code nodes by build_dsl.py."""


def encode_prepare(player_id: str, turn_id: str, action: str) -> dict:
    import json
    return {"body": json.dumps({"player_id": player_id, "turn_id": turn_id, "action": action}, ensure_ascii=False)}


def decode_prepare(body: str, status_code: int) -> dict:
    import json
    if int(status_code) != 200:
        raise ValueError("记忆读取失败，尚未执行本轮互动。请检查后端连接和运行日志。")
    value = json.loads(body)
    return {"history": value["history"], "base_sequence": value["base_sequence"],
            "remembered_turns": value["remembered_turns"]}


def encode_commit(player_id: str, turn_id: str, action: str, reply: str, base_sequence: int) -> dict:
    import json
    # Do not truncate: a rejected response must never be displayed as saved.
    return {"body": json.dumps({"player_id": player_id, "turn_id": turn_id, "action": action,
                                "reply": reply, "base_sequence": int(base_sequence)}, ensure_ascii=False)}


def decode_commit(body: str, status_code: int) -> dict:
    import json
    if int(status_code) != 200:
        raise ValueError("本轮未确认保存。若进度冲突，请重新运行；其他情况请检查后端日志。")
    value = json.loads(body)
    return {"reply": value["reply"], "memory_receipt": json.dumps(value["citation"], ensure_ascii=False),
            "saved_turns": value["sequence"]}
