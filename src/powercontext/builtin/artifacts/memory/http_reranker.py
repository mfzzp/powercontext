"""Batch HTTP scoring with stable global selection of original Memory ranks."""

from __future__ import annotations

import json

import httpx
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat

from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.builtin.artifacts.memory.reranking import MemoryRerankDecision
from powercontext.builtin.inference import InferenceUsage


class _Score(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    rank: int = Field(ge=1)
    score: int | FiniteFloat


class _Response(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    scores: list[_Score]


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object field")  # noqa: TRY003
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"Non-finite JSON number: {value}")


class HTTPMemoryReranker:
    """Score every candidate once with a caller-owned asynchronous client.

    Responses contain global one-based ranks, even for later batches. Only the
    selected ranks are returned, leaving original Memory hit identities intact.
    The adapter does not close the client or retry failed HTTP requests.
    """

    def __init__(
        self,
        endpoint: str,
        *,
        client: httpx.AsyncClient,
        batch_size: int = 32,
        policy_id: str = "http-cross-encoder-v1",
    ) -> None:
        if type(batch_size) is not int or batch_size < 1:
            raise ValueError("batch_size must be a positive integer")  # noqa: TRY003
        self.policy_id = policy_id
        self._endpoint = endpoint
        self._client = client
        self._batch_size = batch_size

    async def rerank(
        self,
        query: str,
        candidates: tuple[MemoryHit, ...],
        limit: int,
        /,
    ) -> MemoryRerankDecision:
        if not candidates:
            raise ValueError("rerank requires at least one candidate")  # noqa: TRY003
        if type(limit) is not int or not 1 <= limit <= len(candidates):
            raise ValueError("limit must be between one and the candidate count")  # noqa: TRY003
        if not isinstance(query, str):
            raise ValueError("query must be a string")  # noqa: TRY003

        scores: dict[int, int | float] = {}
        requests = 0
        for start in range(0, len(candidates), self._batch_size):
            batch = [
                {"rank": index + 1, "text": candidates[index].text}
                for index in range(start, min(start + self._batch_size, len(candidates)))
            ]
            requests += 1
            response = await self._client.post(
                self._endpoint, json={"query": query, "candidates": batch}, follow_redirects=False
            )
            response.raise_for_status()
            payload = json.loads(
                response.content,
                object_pairs_hook=_unique_object,
                parse_constant=_reject_constant,
            )
            parsed = _Response.model_validate(payload)
            expected = {item["rank"] for item in batch}
            actual = [item.rank for item in parsed.scores]
            if len(actual) != len(expected) or set(actual) != expected:
                raise ValueError("Response must cover every rank in this batch exactly once")  # noqa: TRY003
            scores.update((item.rank, item.score) for item in parsed.scores)

        # A single sort over all batches is necessary: a later batch may contain
        # the best candidate, and ties use the original input order globally.
        selected = tuple(sorted(scores, key=lambda rank: (-scores[rank], rank))[:limit])
        return MemoryRerankDecision(selected_ranks=selected, usage=InferenceUsage(requests=requests))


__all__ = ["HTTPMemoryReranker"]
