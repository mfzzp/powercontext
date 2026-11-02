"""Cross-call sharing, batch failure isolation and exact per-caller usage."""

from __future__ import annotations

import asyncio

import pytest

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.inference import EmbeddingResult, InferenceUsage
from powercontext.builtin.inference.coalescing import CoalescingEmbeddingModel


class Model:
    profile = EmbeddingProfile(profile_id="coalescing-regression", model="local", dimension=1)

    def __init__(self):
        self.calls = []

    async def embed(self, texts):
        self.calls.append(texts)
        return EmbeddingResult(
            vectors=tuple((float(len(text)),) for text in texts),
            usage=InferenceUsage(requests=2, input_tokens=7, output_tokens=3),
        )


async def test_late_waiter_shares_executing_batch_without_cache_and_counts_usage_once():
    class Blocked(Model):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def embed(self, texts):
            self.started.set()
            await self.release.wait()
            return await super().embed(texts)

    base = Blocked()
    model = CoalescingEmbeddingModel(base, cache_size=0, coalesce_ms=0)
    assert model.profile is base.profile
    first = asyncio.create_task(model.embed(("one", "second")))
    await asyncio.wait_for(base.started.wait(), timeout=2)
    late = asyncio.create_task(model.embed(("second", "one", "second")))
    await asyncio.sleep(0)
    base.release.set()
    a, b = await asyncio.wait_for(asyncio.gather(first, late), timeout=2)
    assert base.calls == [("one", "second")]
    assert a.vectors == ((3.0,), (6.0,))
    assert b.vectors == ((6.0,), (3.0,), (6.0,))
    assert a.usage == b.usage == InferenceUsage(requests=2, input_tokens=7, output_tokens=3)
    await model.embed(("one",))
    assert base.calls[-1] == ("one",) and len(base.calls) == 2


async def test_multi_batch_usage_counts_only_batches_this_call_waited_for():
    base = Model()
    model = CoalescingEmbeddingModel(base, max_batch_size=2, coalesce_ms=5)
    first, second = await asyncio.gather(
        model.embed(("a", "bb", "ccc", "a")), model.embed(("bb", "a"))
    )
    assert base.calls == [("a", "bb"), ("ccc",)]
    assert first.usage == InferenceUsage(requests=4, input_tokens=14, output_tokens=6)
    assert second.usage == InferenceUsage(requests=2, input_tokens=7, output_tokens=3)
    mixed = await model.embed(("a", "new", "bb", "new"))
    assert mixed.vectors == ((1.0,), (3.0,), (2.0,), (3.0,))
    assert mixed.usage == InferenceUsage(requests=2, input_tokens=7, output_tokens=3)
    cached = await model.embed(("new", "a"))
    assert cached.usage.requests == 0 and len(base.calls) == 3


async def test_a_failed_batch_does_not_poison_independent_success_and_can_retry():
    class Partial(Model):
        fail = True

        async def embed(self, texts):
            if self.fail and "bad" in texts:
                self.calls.append(texts)
                raise RuntimeError("controlled failure")
            return await super().embed(texts)

    base = Partial()
    model = CoalescingEmbeddingModel(base, max_batch_size=1, coalesce_ms=0)
    failed, success = await asyncio.gather(model.embed(("bad",)), model.embed(("good",)), return_exceptions=True)
    assert isinstance(failed, RuntimeError)
    assert success.vectors == ((4.0,),)
    assert (await model.embed(("good",))).usage.requests == 0
    base.fail = False
    assert (await model.embed(("bad",))).vectors == ((3.0,),)
    assert base.calls.count(("bad",)) == 2


async def test_wrong_vector_count_fails_all_shared_waiters_and_does_not_cache():
    class WrongCount(Model):
        broken = True

        async def embed(self, texts):
            if self.broken:
                self.calls.append(texts)
                return EmbeddingResult(vectors=())
            return await super().embed(texts)

    base = WrongCount()
    model = CoalescingEmbeddingModel(base, coalesce_ms=0)
    results = await asyncio.gather(model.embed(("shared",)), model.embed(("shared",)), return_exceptions=True)
    assert all(isinstance(result, ValueError) for result in results)
    assert len(base.calls) == 1
    base.broken = False
    assert (await model.embed(("shared",))).vectors == ((6.0,),)
    assert len(base.calls) == 2


async def test_cancelled_call_keeps_all_its_batches_available_to_other_waiters():
    class Blocked(Model):
        def __init__(self):
            super().__init__()
            self.release = asyncio.Event()
            self.started = asyncio.Event()

        async def embed(self, texts):
            self.started.set()
            await self.release.wait()
            return await super().embed(texts)

    base = Blocked()
    model = CoalescingEmbeddingModel(base, max_batch_size=1, coalesce_ms=0)
    cancelled = asyncio.create_task(model.embed(("a", "bb")))
    await asyncio.wait_for(base.started.wait(), timeout=2)
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    survivor = asyncio.create_task(model.embed(("bb", "a", "bb")))
    await asyncio.sleep(0)
    base.release.set()
    result = await asyncio.wait_for(survivor, timeout=2)
    assert result.vectors == ((2.0,), (1.0,), (2.0,))
    assert result.usage.requests == 4
    assert sorted(base.calls) == [("a",), ("bb",)]


async def test_small_cache_eviction_does_not_drop_current_call_results():
    base = Model()
    model = CoalescingEmbeddingModel(base, cache_size=1, max_batch_size=3, coalesce_ms=0)
    result = await model.embed(("a", "bb", "ccc", "a"))
    assert result.vectors == ((1.0,), (2.0,), (3.0,), (1.0,))
    assert (await model.embed(("ccc",))).usage.requests == 0
    await model.embed(("a",))
    assert base.calls == [("a", "bb", "ccc"), ("a",)]


async def test_query_specific_provider_does_not_share_document_vector_cache():
    class Asymmetric(Model):
        def __init__(self):
            super().__init__()
            self.query_calls = []

        async def embed_query(self, texts):
            self.query_calls.append(texts)
            return EmbeddingResult(vectors=tuple((-float(len(text)),) for text in texts), usage=InferenceUsage(requests=1))

    base = Asymmetric()
    model = CoalescingEmbeddingModel(base, coalesce_ms=0)
    assert (await model.embed(("same",))).vectors == ((4.0,),)
    assert (await model.embed_query(("same",))).vectors == ((-4.0,),)
    assert (await model.embed_query(("same",))).usage.requests == 0
    assert base.calls == base.query_calls == [("same",)]


@pytest.mark.parametrize("settings", [
    {"max_batch_size": 0}, {"max_batch_size": True}, {"max_batch_size": 1.5},
    {"cache_size": -1}, {"cache_size": False}, {"cache_size": "1"},
    {"coalesce_ms": -1}, {"coalesce_ms": True}, {"coalesce_ms": "1"},
    {"coalesce_ms": float("nan")}, {"coalesce_ms": float("inf")},
])
def test_invalid_settings_fail_without_model_work(settings):
    base = Model()
    with pytest.raises(ValueError):
        CoalescingEmbeddingModel(base, **settings)
    assert base.calls == []
