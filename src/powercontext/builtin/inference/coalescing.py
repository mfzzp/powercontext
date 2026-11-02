"""Share pending embeddings across calls and cache successful vectors with LRU."""

from __future__ import annotations

import asyncio
import math
from collections import OrderedDict
from dataclasses import dataclass

from powercontext.builtin.inference.models import EmbeddingResult, EmbeddingVector, InferenceUsage
from powercontext.builtin.inference.protocols import EmbeddingModel, QueryEmbeddingModel

_Key = tuple[bool, str]


@dataclass(frozen=True, slots=True)
class _EmbeddedText:
    vector: EmbeddingVector
    batch_id: int
    usage: InferenceUsage


def _observe_exception(future: asyncio.Future[_EmbeddedText]) -> None:
    # A caller may cancel before work finishes, leaving no active waiter.
    # Observing the exception prevents orphan-future warnings without hiding it
    # from any other caller awaiting the same Future.
    if not future.cancelled():
        future.exception()


class CoalescingEmbeddingModel:
    """Coordinate one model within one event loop; callers never own shared tasks."""

    def __init__(
        self,
        model: EmbeddingModel,
        *,
        max_batch_size: int = 64,
        coalesce_ms: float = 5.0,
        cache_size: int = 1024,
    ) -> None:
        if type(max_batch_size) is not int or max_batch_size < 1:
            raise ValueError("invalid-max-batch-size")
        if type(cache_size) is not int or cache_size < 0:
            raise ValueError("invalid-cache-size")
        if (
            isinstance(coalesce_ms, bool)
            or not isinstance(coalesce_ms, (int, float))
            or not math.isfinite(coalesce_ms)
            or coalesce_ms < 0
        ):
            raise ValueError("invalid-coalesce-window")
        self.profile = model.profile
        self._model = model
        self._max_batch_size = max_batch_size
        self._delay = coalesce_ms / 1000
        self._cache_size = cache_size
        self._cache: OrderedDict[_Key, EmbeddingVector] = OrderedDict()
        self._work: dict[_Key, asyncio.Future[_EmbeddedText]] = {}
        self._pending: dict[_Key, asyncio.Future[_EmbeddedText]] = {}
        self._scheduled: asyncio.TimerHandle | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._next_batch_id = 0
        self._loop: asyncio.AbstractEventLoop | None = None

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        return await self._embed(texts, query=False)

    async def embed_query(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        # Symmetric models share document/query cache hits. Providers with
        # distinct query embeddings must retain separate work/cache namespaces.
        return await self._embed(texts, query=isinstance(self._model, QueryEmbeddingModel))

    async def _embed(self, texts: tuple[str, ...], *, query: bool) -> EmbeddingResult:
        if not isinstance(texts, (tuple, list)) or any(not isinstance(text, str) for text in texts):
            raise ValueError("texts-must-be-a-string-sequence")
        if not texts:
            return EmbeddingResult(vectors=(), usage=InferenceUsage(requests=0, input_tokens=0, output_tokens=0))
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            raise ValueError("wrapper-belongs-to-another-event-loop")
        self._loop = loop

        vectors: dict[str, EmbeddingVector] = {}
        waiting: dict[str, asyncio.Future[_EmbeddedText]] = {}
        # No await occurs while registering work, so simultaneous calls cannot
        # race to create two Futures for the same key in this event loop.
        for text in texts:
            key = (query, text)
            if key in self._cache:
                vectors[text] = self._cache[key]
                self._cache.move_to_end(key)
                continue
            future = self._work.get(key)
            if future is None:
                future = loop.create_future()
                future.add_done_callback(_observe_exception)
                self._work[key] = future
                self._pending[key] = future
            waiting[text] = future

        if self._pending and self._scheduled is None:
            self._scheduled = loop.call_later(self._delay, self._dispatch)
        batches: dict[int, InferenceUsage] = {}
        if waiting:
            results = await asyncio.gather(*(asyncio.shield(future) for future in waiting.values()))
            for text, result in zip(waiting, results, strict=True):
                vectors[text] = result.vector
                batches.setdefault(result.batch_id, result.usage)
        usage = InferenceUsage(
            requests=sum(batch.requests for batch in batches.values()),
            input_tokens=_sum_tokens(tuple(batch.input_tokens for batch in batches.values())),
            output_tokens=_sum_tokens(tuple(batch.output_tokens for batch in batches.values())),
        )
        return EmbeddingResult(vectors=tuple(vectors[text] for text in texts), usage=usage)

    def _dispatch(self) -> None:
        self._scheduled = None
        pending, self._pending = self._pending, {}
        # Never mix provider-specific query and document operations in one batch.
        for query in (False, True):
            items = [(key, future) for key, future in pending.items() if key[0] == query]
            for start in range(0, len(items), self._max_batch_size):
                self._next_batch_id += 1
                task = asyncio.create_task(
                    self._run_batch(items[start:start + self._max_batch_size], query, self._next_batch_id)
                )
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)

    async def _run_batch(
        self, items: list[tuple[_Key, asyncio.Future[_EmbeddedText]]], query: bool, batch_id: int
    ) -> None:
        try:
            texts = tuple(key[1] for key, _ in items)
            if query and isinstance(self._model, QueryEmbeddingModel):
                result = await self._model.embed_query(texts)
            else:
                result = await self._model.embed(texts)
            if len(result.vectors) != len(items):
                raise ValueError("provider-vector-count-mismatch")
            usage = result.usage.model_copy(deep=True)
            for (key, future), vector in zip(items, result.vectors, strict=True):
                if self._cache_size:
                    self._cache[key] = vector
                    self._cache.move_to_end(key)
                    while len(self._cache) > self._cache_size:
                        self._cache.popitem(last=False)
                if not future.done():
                    future.set_result(_EmbeddedText(vector=vector, batch_id=batch_id, usage=usage))
        except asyncio.CancelledError:
            for _, future in items:
                if not future.done():
                    future.cancel()
            raise
        except Exception as error:
            for _, future in items:
                if not future.done():
                    future.set_exception(error)
        finally:
            for key, future in items:
                if self._work.get(key) is future:
                    self._work.pop(key)


def _sum_tokens(values: tuple[int | None, ...]) -> int | None:
    if not values:
        return 0
    known = tuple(value for value in values if value is not None)
    return None if not known else sum(known)


__all__ = ["CoalescingEmbeddingModel"]
