import json
from pathlib import Path
import unittest

import yaml
from node_code import decode_commit, decode_prepare, encode_commit, encode_prepare


class WorkflowTests(unittest.TestCase):
    def test_serialized_nodes_and_unicode_payload(self):
        graph = yaml.safe_load((Path(__file__).parents[1] / "grudge-shop.yml").read_text(encoding="utf-8"))["workflow"]["graph"]
        nodes = {node["id"]: node["data"] for node in graph["nodes"]}
        self.assertEqual(len(graph["edges"]), len(nodes) - 1)
        for node in nodes.values():
            if node["type"] == "code":
                namespace = {}
                exec(compile(node["code"], "dsl-code-node", "exec"), namespace)
                self.assertTrue(callable(namespace["main"]))
        action = '把价签改成"喵"\n再道歉'
        self.assertEqual(json.loads(encode_prepare("p1", "t1", action)["body"])["action"], action)
        payload = encode_commit("p1", "t1", action, "固定台词", 0)
        self.assertEqual(json.loads(payload["body"])["reply"], "固定台词")
        for status in (401, 409, 500):
            with self.assertRaises(ValueError):
                decode_prepare('{"error":"failed"}', status)
            with self.assertRaises(ValueError):
                decode_commit('{"error":"failed"}', status)


if __name__ == "__main__":
    unittest.main(verbosity=2)
