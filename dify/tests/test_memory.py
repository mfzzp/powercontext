"""Real PowerContext tests; NPC replies here are explicit fixtures, not LLM evidence."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest

from npc_service import Game, GameError


class MemoryTests(unittest.TestCase):
    def test_restart_isolation_replay_and_stale_write(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            request = {"player_id": "alice", "turn_id": "t1", "action": '把价签改成"一声喵"\n然后挥手'}
            first = asyncio.run(Game(path).prepare(request))
            self.assertEqual(first["remembered_turns"], 0)
            payload = dict(request, reply="测试固定台词：阿柜把价签贴回去。", base_sequence=0)
            written = asyncio.run(Game(path).commit(payload))
            self.assertFalse(written["replayed"])
            # Every method opens/closes the Runtime; a fresh Game has no shared state.
            second = asyncio.run(Game(path).prepare(dict(request, turn_id="t2", action="我又来了")))
            self.assertEqual(json.loads(second["history"])[0]["action"], request["action"])
            self.assertEqual(second["remembered_turns"], 1)
            replay = asyncio.run(Game(path).commit(dict(payload, reply="应当被忽略的重复生成")))
            self.assertEqual(replay["citation"], written["citation"])
            self.assertEqual(replay["reply"], payload["reply"])
            self.assertTrue(replay["replayed"])
            other = asyncio.run(Game(path).prepare(dict(request, player_id="bob")))
            self.assertEqual(other["remembered_turns"], 0)
            with self.assertRaises(GameError) as conflict:
                asyncio.run(Game(path).commit(dict(payload, turn_id="t2")))
            self.assertEqual(conflict.exception.status, 409)
            with self.assertRaises(GameError):
                asyncio.run(Game(path).prepare(dict(request, action="改写已提交行动")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
