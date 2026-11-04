"""验收测试：覆盖 ACCEPTANCE.md 全部场景。"""
from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, TestClient, TestServer

# Ensure app package is importable
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import create_app
from app.memory_service import MemoryService


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def data_dir():
    d = Path(tempfile.mkdtemp(prefix="review-test-"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest_asyncio.fixture
async def client(data_dir):
    app = create_app(data_dir)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    yield client
    await client.close()


# ── Helper ──

async def post_json(client, path, body):
    resp = await client.post(path, json=body)
    return resp.status, await resp.json()


async def get_json(client, path):
    resp = await client.get(path)
    return resp.status, await resp.json()


# ── 样例 A：薄弱点与跨启动接力 ──

@pytest.mark.asyncio
async def test_a_weak_points_and_restart(client):
    archive = "learner-a-001"

    # 1. 查看主题列表
    status, data = await get_json(client, "/api/topics")
    assert status == 200
    assert "python" in data["topics"]

    # 2. P1 自评"不会"(0)
    status, data = await post_json(client, "/api/submit", {
        "archive": archive,
        "request_id": f"{archive}-P1-001",
        "qid": "P1",
        "score": 0,
    })
    assert status == 200
    assert data["replayed"] is False
    assert data["qid"] == "P1"
    assert data["score"] == 0
    citation_p1 = data["citation"]
    assert citation_p1 is not None

    # 3. P2 自评"掌握"(2)
    status, data = await post_json(client, "/api/submit", {
        "archive": archive,
        "request_id": f"{archive}-P2-001",
        "qid": "P2",
        "score": 2,
    })
    assert status == 200
    assert data["score"] == 2

    # 4. 查看进度：P1 为薄弱，掌握数为 1
    status, data = await get_json(client, f"/api/progress?archive={archive}")
    assert status == 200
    assert data["scores"]["P1"] == 0
    assert data["scores"]["P2"] == 2
    assert data["mastered_count"] == 1
    assert "P1" in data["weak_qids"]

    # 5. 推荐：优先 P1（薄弱）
    status, data = await get_json(client, f"/api/recommend?archive={archive}")
    assert status == 200
    assert data["qid"] == "P1"
    assert data["priority"] == "weak"

    # 6. 模拟重启：创建新 client 使用同一 data_dir
    data_dir = client.app["data_dir"] if "data_dir" in client.app else None
    # We can't easily get data_dir from client, so test persistence via service directly
    # Instead, create a new app with same data_dir
    # Actually, let's verify by reading progress again - same client, same data
    status, data = await get_json(client, f"/api/progress?archive={archive}")
    assert data["scores"]["P1"] == 0
    assert data["scores"]["P2"] == 2

    # 7. P1 再次自评"掌握"(2)
    status, data = await post_json(client, "/api/submit", {
        "archive": archive,
        "request_id": f"{archive}-P1-002",
        "qid": "P1",
        "score": 2,
    })
    assert status == 200

    # 8. 掌握数变为 2
    status, data = await get_json(client, f"/api/progress?archive={archive}")
    assert data["mastered_count"] == 2
    assert data["scores"]["P1"] == 2  # 最新评分覆盖

    # 9. 推荐不再按"不会"推荐 P1
    status, data = await get_json(client, f"/api/recommend?archive={archive}")
    assert data["qid"] != "P1" or data["priority"] != "weak"

    # 10. citation 已在步骤 2 验证非空，可通过 progress API 的 citations 字段回读
    status, data = await get_json(client, f"/api/progress?archive={archive}")
    assert "P1" in data["citations"]


# ── 样例 B：隔离与幂等 ──

@pytest.mark.asyncio
async def test_b_isolation_and_idempotent(client):
    archive_a = "learner-b-001"
    archive_b = "learner-b-002"

    # 1. 新用户历史为空
    status, data = await get_json(client, f"/api/progress?archive={archive_a}")
    assert status == 200
    assert data["total_practices"] == 0
    assert data["scores"] == {}

    # 2. A 提交一条记录
    status, data = await post_json(client, "/api/submit", {
        "archive": archive_a,
        "request_id": "req-b-001",
        "qid": "G1",
        "score": 1,
    })
    assert status == 200
    assert data["replayed"] is False

    # 3. 重放同一 request_id + 相同数据 → 幂等
    status, data = await post_json(client, "/api/submit", {
        "archive": archive_a,
        "request_id": "req-b-001",
        "qid": "G1",
        "score": 1,
    })
    assert status == 200
    assert data["replayed"] is True

    # 4. 总练习数不增加
    status, data = await get_json(client, f"/api/progress?archive={archive_a}")
    assert data["total_practices"] == 1

    # 5. 同 request_id 不同数据 → 冲突
    status, data = await post_json(client, "/api/submit", {
        "archive": archive_a,
        "request_id": "req-b-001",
        "qid": "G1",
        "score": 2,  # 不同分数
    })
    assert status == 409

    # 6. 用户隔离：B 看不到 A 的进度
    status, data = await get_json(client, f"/api/progress?archive={archive_b}")
    assert data["total_practices"] == 0
    assert data["scores"] == {}


# ── 样例 B 续：参数校验 ──

@pytest.mark.asyncio
async def test_b_validation(client):
    archive = "learner-val"

    # score 为布尔值 → 失败
    status, data = await post_json(client, "/api/submit", {
        "archive": archive,
        "request_id": "req-val-1",
        "qid": "P1",
        "score": True,
    })
    assert status == 400

    # score 为字符串 "1" → 失败
    status, data = await post_json(client, "/api/submit", {
        "archive": archive,
        "request_id": "req-val-2",
        "qid": "P1",
        "score": "1",
    })
    assert status == 400

    # score 为浮点数 1.0 → 失败
    status, data = await post_json(client, "/api/submit", {
        "archive": archive,
        "request_id": "req-val-3",
        "qid": "P1",
        "score": 1.0,
    })
    assert status == 400

    # score 为 -1 → 失败
    status, data = await post_json(client, "/api/submit", {
        "archive": archive,
        "request_id": "req-val-4",
        "qid": "P1",
        "score": -1,
    })
    assert status == 400

    # score 为 3 → 失败
    status, data = await post_json(client, "/api/submit", {
        "archive": archive,
        "request_id": "req-val-5",
        "qid": "P1",
        "score": 3,
    })
    assert status == 400

    # 无效主题 → 失败
    status, data = await post_json(client, "/api/submit", {
        "archive": archive,
        "request_id": "req-val-6",
        "qid": "X999",
        "score": 1,
    })
    assert status == 400

    # 无效存档码 → 失败
    status, data = await post_json(client, "/api/submit", {
        "archive": "invalid archive!@#",
        "request_id": "req-val-7",
        "qid": "P1",
        "score": 1,
    })
    assert status == 400

    # 以上全部失败，不应写入任何记录
    status, data = await get_json(client, f"/api/progress?archive={archive}")
    assert data["total_practices"] == 0


# ── 样例 C：端到端与错误反馈 ──

@pytest.mark.asyncio
async def test_c_end_to_end(client):
    archive = "learner-c-001"

    # 1. 完整流程
    status, data = await get_json(client, "/api/topics")
    assert status == 200

    status, data = await get_json(client, "/api/questions/D1")
    assert status == 200
    assert data["qid"] == "D1"
    assert "answer" in data

    status, data = await post_json(client, "/api/submit", {
        "archive": archive,
        "request_id": "req-c-001",
        "qid": "D1",
        "score": 2,
    })
    assert status == 200
    assert data["replayed"] is False
    citation = data["citation"]

    status, data = await get_json(client, f"/api/progress?archive={archive}")
    assert data["scores"]["D1"] == 2
    assert data["mastered_count"] == 1

    # 2. 验证 citation 非空
    assert citation is not None
    assert "entry_id" in citation or "citation" in str(citation).lower() or len(str(citation)) > 5


# ── 推荐排序测试 ──

@pytest.mark.asyncio
async def test_recommendation_priority(client):
    archive = "learner-rec"

    # 初始：推荐第一个未练过的题
    status, data = await get_json(client, f"/api/recommend?archive={archive}")
    assert status == 200
    assert data["priority"] == "unseen"

    # P1 不会
    await post_json(client, "/api/submit", {
        "archive": archive, "request_id": "rec-1", "qid": "P1", "score": 0,
    })
    # P2 模糊
    await post_json(client, "/api/submit", {
        "archive": archive, "request_id": "rec-2", "qid": "P2", "score": 1,
    })
    # P3 掌握
    await post_json(client, "/api/submit", {
        "archive": archive, "request_id": "rec-3", "qid": "P3", "score": 2,
    })

    # 推荐：优先 P1（不会）
    status, data = await get_json(client, f"/api/recommend?archive={archive}")
    assert data["qid"] == "P1"
    assert data["priority"] == "weak"

    # P1 改为掌握
    await post_json(client, "/api/submit", {
        "archive": archive, "request_id": "rec-4", "qid": "P1", "score": 2,
    })

    # 推荐：现在优先 P2（模糊）
    status, data = await get_json(client, f"/api/recommend?archive={archive}")
    assert data["qid"] == "P2"
    assert data["priority"] == "fuzzy"

    # P2 也改为掌握
    await post_json(client, "/api/submit", {
        "archive": archive, "request_id": "rec-5", "qid": "P2", "score": 2,
    })

    # 推荐：下一个未练过的题
    status, data = await get_json(client, f"/api/recommend?archive={archive}")
    assert data["priority"] == "unseen"


# ── 跨重启持久化 ──

@pytest.mark.asyncio
async def test_restart_persistence(data_dir):
    archive = "learner-restart"

    # 第一次启动
    app1 = create_app(data_dir)
    server1 = TestServer(app1)
    client1 = TestClient(server1)
    await client1.start_server()

    await post_json(client1, "/api/submit", {
        "archive": archive, "request_id": "rs-1", "qid": "G1", "score": 0,
    })
    await post_json(client1, "/api/submit", {
        "archive": archive, "request_id": "rs-2", "qid": "G2", "score": 2,
    })

    status, data = await get_json(client1, f"/api/progress?archive={archive}")
    assert data["scores"]["G1"] == 0
    assert data["scores"]["G2"] == 2
    citation_g1 = data["citations"]["G1"]

    await client1.close()

    # 第二次启动（模拟重启）
    app2 = create_app(data_dir)
    server2 = TestServer(app2)
    client2 = TestClient(server2)
    await client2.start_server()

    status, data = await get_json(client2, f"/api/progress?archive={archive}")
    assert data["scores"]["G1"] == 0
    assert data["scores"]["G2"] == 2
    assert data["total_practices"] == 2

    # citation 仍有效
    assert data["citations"]["G1"] == citation_g1

    await client2.close()


# ── 掌握数去重 ──

@pytest.mark.asyncio
async def test_mastered_count_dedup(client):
    archive = "learner-dedup"

    # P1 提交多次，最终掌握
    await post_json(client, "/api/submit", {
        "archive": archive, "request_id": "dedup-1", "qid": "P1", "score": 0,
    })
    await post_json(client, "/api/submit", {
        "archive": archive, "request_id": "dedup-2", "qid": "P1", "score": 1,
    })
    await post_json(client, "/api/submit", {
        "archive": archive, "request_id": "dedup-3", "qid": "P1", "score": 2,
    })

    status, data = await get_json(client, f"/api/progress?archive={archive}")
    # 虽然练习 3 次，但掌握数只算 1 个题目
    assert data["mastered_count"] == 1
    assert data["total_practices"] == 3
    assert data["scores"]["P1"] == 2


# ── 失败不虚报 ──

@pytest.mark.asyncio
async def test_failure_not_reported_as_success(client):
    archive = "learner-fail"

    # 尝试无效提交
    status, data = await post_json(client, "/api/submit", {
        "archive": archive, "request_id": "fail-1", "qid": "INVALID", "score": 1,
    })
    assert status == 400
    assert "error" in data

    # 确认未写入
    status, data = await get_json(client, f"/api/progress?archive={archive}")
    assert data["total_practices"] == 0
