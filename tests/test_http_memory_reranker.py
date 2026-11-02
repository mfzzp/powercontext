"""Boundary and integration regressions for HTTP Memory reranking."""

from __future__ import annotations

import json

import httpx
import pytest

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory.http_reranker import HTTPMemoryReranker
from powercontext.builtin.artifacts.memory.models import MemoryHit


def hits(count):
    return tuple(
        MemoryHit(
            memory_ref=ArtifactRef(family="memory", artifact_id="memory", revision=1),
            entry_id=f"e{rank}", entry_version_id=f"v{rank}", text=f"原始正文 {rank}",
            score=1 / rank, matched_by=("fts",),
        )
        for rank in range(1, count + 1)
    )


async def test_173_candidates_batch_seven_coverage_global_ties_and_usage():
    original = hits(173)
    snapshots = tuple(hit.model_dump() for hit in original)
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        assert set(body) == {"query", "candidates"}
        assert body["query"] == "查询"
        assert 1 <= len(body["candidates"]) <= 7
        for item in body["candidates"]:
            assert set(item) == {"rank", "text"}
            assert item["text"] == original[item["rank"] - 1].text
        return httpx.Response(200, json={
            "scores": [{"rank": item["rank"], "score": item["rank"] % 13} for item in reversed(body["candidates"])],
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = HTTPMemoryReranker("https://test.invalid/score", client=client, batch_size=7, policy_id="custom")
        result = await adapter.rerank("查询", original, 10)
        assert not client.is_closed
        assert adapter.policy_id == "custom"
    expected = tuple(sorted(range(1, 174), key=lambda rank: (-(rank % 13), rank))[:10])
    assert result.selected_ranks == expected
    assert result.usage.requests == len(requests) == 25
    assert sorted(item["rank"] for body in requests for item in body["candidates"]) == list(range(1, 174))
    assert tuple(hit.model_dump() for hit in original) == snapshots


@pytest.mark.parametrize("batch_size", [0, -1, True, False, 1.5, "2"])
async def test_invalid_batch_size(batch_size):
    async with httpx.AsyncClient() as client:
        with pytest.raises(ValueError):
            HTTPMemoryReranker("https://test.invalid", client=client, batch_size=batch_size)


@pytest.mark.parametrize("limit", [0, -1, 3, True, 1.5, "1"])
async def test_invalid_limit_fails_before_sending(limit):
    def handler(request):
        pytest.fail("Invalid input must fail before an HTTP request")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError):
            await HTTPMemoryReranker("https://test.invalid", client=client).rerank("q", hits(2), limit)


@pytest.mark.parametrize("payload", [
    b"not JSON", b"[]", b"null", b"{}", b'{"scores":{}}',
    b'{"scores":[{"rank":true,"score":1}]}',
    b'{"scores":[{"rank":1.0,"score":1}]}',
    b'{"scores":[{"rank":"1","score":1}]}',
    b'{"scores":[{"rank":1,"score":true}]}',
    b'{"scores":[{"rank":1,"score":"0.8"}]}',
    b'{"scores":[{"rank":1,"score":null}]}',
    b'{"scores":[{"rank":1,"score":1e999}]}',
    b'{"scores":[{"rank":1,"score":NaN}]}',
    b'{"scores":[{"rank":1,"score":-Infinity}]}',
    b'{"scores":[],"scores":[{"rank":1,"score":1}]}',
    b'{"scores":[{"rank":1,"score":1,"score":2}]}',
])
async def test_strict_invalid_responses(payload):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=payload))) as client:
        with pytest.raises(ValueError):
            await HTTPMemoryReranker("https://test.invalid", client=client).rerank("q", hits(1), 1)


async def test_rank_from_another_batch_is_rejected():
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        return httpx.Response(200, json={"scores": [{"rank": 1, "score": 0.5}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError):
            await HTTPMemoryReranker("https://test.invalid", client=client, batch_size=1).rerank("q", hits(2), 1)
    assert count == 2


@pytest.mark.parametrize("status", [301, 400, 429, 500])
async def test_non_success_preserves_http_status_exception(status):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(status))) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await HTTPMemoryReranker("https://test.invalid", client=client).rerank("q", hits(1), 1)


async def test_network_timeout_is_not_wrapped_or_retried():
    requests = 0

    def handler(request):
        nonlocal requests
        requests += 1
        raise httpx.ReadTimeout("test timeout", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.ReadTimeout):
            await HTTPMemoryReranker("https://test.invalid", client=client).rerank("q", hits(3), 1)
    assert requests == 1


async def test_large_integer_scores_do_not_lose_ordering_precision():
    def handler(request):
        return httpx.Response(200, json={"scores": [
            {"rank": 1, "score": 2 ** 60}, {"rank": 2, "score": 2 ** 60 + 1},
        ]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await HTTPMemoryReranker("https://test.invalid", client=client).rerank("q", hits(2), 2)
    assert result.selected_ranks == (2, 1)


async def test_redirects_are_errors_even_with_redirect_enabled_client():
    requests = 0

    def handler(request):
        nonlocal requests
        requests += 1
        return httpx.Response(302, headers={"Location": "https://test.invalid/other"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await HTTPMemoryReranker("https://test.invalid", client=client).rerank("q", hits(1), 1)
    assert requests == 1
