"""Literal evidence, cross-scope identities and complete output budget regressions."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from powercontext.artifacts import ArtifactRef, MemoryCitation
from powercontext.builtin.artifacts.memory import MemoryEntryInput, MemoryHit
from powercontext.builtin.runtime import (
    BuiltinConfig,
    ContextAssembly,
    GetMemoryEntryRequest,
    PrepareContextRequest,
    RememberMemoryRequest,
    open_builtin_runtime,
)
from powercontext.builtin.runtime.errors import PreparedContextInvariantError
from powercontext.builtin.runtime.prepared_context import (
    PreparedContextBuilder,
    PreparedEvidenceFragment,
    PreparedMemoryCandidates,
)
from powercontext.builtin.runtime.prepared_text import TRUST_POLICY
from powercontext.builtin.scope import ScopeDraft


def group(texts, scope="scope-a"):
    reference = ArtifactRef(family="memory", artifact_id="memory", revision=3)
    return PreparedMemoryCandidates(scope_id=scope, memory_ref=reference, hits=tuple(
        MemoryHit(memory_ref=reference, entry_id=f"entry-{index}", entry_version_id=f"version-{index}",
                  text=text, score=1.0, matched_by=("fts",))
        for index, text in enumerate(texts)
    ))


def build(groups, query, budget=1400, **settings):
    return PreparedContextBuilder().build_scopes_result(
        request=PrepareContextRequest(query=query, max_bytes=budget, evidence_selection=True, **settings),
        current_scope_id="scope-a", memory_candidates=groups,
    )


def verify(result, groups, budget):
    assert result.context.content_bytes == len((result.context.content or "").encode("utf-8")) <= budget
    if result.context.content is None:
        assert not result.fragments and not result.origins
        return
    assert result.context.content.startswith(TRUST_POLICY)
    assert "BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1\n" in result.context.content
    assert result.context.content.endswith("END_POWERCONTEXT_PREPARED_CONTEXT_V1")
    payload = json.loads(result.context.content.splitlines()[-2])
    assert payload["trust"] == "untrusted_history"
    assert len(payload["items"]) == len(result.origins) == len(result.fragments)
    for item, fragment, origin in zip(payload["items"], result.fragments, result.origins, strict=True):
        assert origin == fragment.address
        source = next(hit for candidate in groups for hit in candidate.hits
                      if candidate.scope_id == fragment.address.memory.scope_id
                      and hit.memory_ref == fragment.address.memory.artifact
                      and hit.entry_id == fragment.address.entry_id)
        assert type(fragment.start) is int and type(fragment.end) is int
        assert 0 <= fragment.start < fragment.end <= len(source.text)
        assert source.text[fragment.start:fragment.end] == fragment.text == item["content"]
        assert item["start"] == fragment.start and item["end"] == fragment.end
        assert item["citation"] == {
            "scope_id": fragment.address.memory.scope_id,
            "memory_ref": source.memory_ref.model_dump(mode="json"),
            "entry_id": source.entry_id, "entry_version_id": source.entry_version_id,
        }


def test_two_nonadjacent_evidence_intervals_and_query_switch():
    first = "The cobalt sensor measures 37 degrees."
    last = "Cobalt calibration requires nitrogen at 8 bars."
    unrelated = "Administrative records contain office schedules. " * 30
    groups = (group((first + " " + unrelated + last,
                     "The amber valve opens at 19 degrees.")),)
    result = build(groups, "cobalt sensor calibration nitrogen", 1400)
    verify(result, groups, 1400)
    assert any(first in fragment.text for fragment in result.fragments)
    assert any(last in fragment.text for fragment in result.fragments)
    assert len(result.fragments) >= 2
    alternative = build(groups, "amber valve opens", 1400)
    verify(alternative, groups, 1400)
    assert any("19 degrees" in fragment.text for fragment in alternative.fragments)


@pytest.mark.parametrize("budget", [512, 650, 700, 800, 1000, 1200, 1400, 1800, 2400])
def test_unicode_json_escaping_and_budget_probe_match_actual_wire(budget):
    text = '日常说明。\n' * 20 + '海洋观测 station records "温度🌡️" at 37 degrees.\n'
    groups = (group((text,)),)
    result = build(groups, "海洋观测 station 温度", budget)
    verify(result, groups, budget)
    request = PrepareContextRequest(query="海洋观测 station 温度", max_bytes=budget, evidence_selection=True)
    probe = PreparedContextBuilder().probe_budget(request=request, current_scope_id="scope-a", memory_candidates=groups)
    assert probe.delivered_items == len(result.fragments)
    assert probe.unused_bytes == budget - result.context.content_bytes


def test_identical_text_in_two_scopes_keeps_distinct_exact_identities():
    groups = (group(("Cobalt sensor calibration requires nitrogen.",), scope="scope-a"),
              group(("Cobalt sensor calibration requires nitrogen.",), scope="scope-b"))
    result = build(groups, "cobalt sensor nitrogen", 2400)
    verify(result, groups, 2400)
    assert {fragment.address.memory.scope_id for fragment in result.fragments} == {"scope-a", "scope-b"}


def test_empty_candidates_and_too_small_budget_are_legal_empty_results():
    assert build((), "sensor").context.content is None
    groups = (group(("Sensor calibration evidence.",)),)
    result = build(groups, "sensor calibration", 512)
    verify(result, groups, 512)
    assert result.context.status == "empty"


def test_long_single_sentence_retains_late_evidence_in_a_literal_window():
    text = "routine administrative notes " * 120 + "cobalt sensor calibrated at 37 degrees"
    groups = (group((text,)),)
    result = build(groups, "cobalt sensor calibrated", 1200)
    verify(result, groups, 1200)
    assert any("37 degrees" in fragment.text for fragment in result.fragments)


def test_overlong_tokens_do_not_hide_the_short_evidence_between_them():
    padding = ("administrative" * 12 + " ") * 30
    groups = (group((padding + "Cobalt sensor measures 37 degrees " + padding,)),)
    result = build(groups, "cobalt sensor measures", 1000)
    verify(result, groups, 1000)
    assert any("37 degrees" in fragment.text for fragment in result.fragments)


def test_default_mode_is_identical_and_evidence_mode_owns_its_envelope():
    groups = (group(("Cobalt sensor calibration evidence.",)),)
    builder = PreparedContextBuilder()
    settings = dict(query="cobalt", max_bytes=1400)
    ordinary = builder.build_scopes_result(request=PrepareContextRequest(**settings), current_scope_id="scope-a",
                                          memory_candidates=groups)
    explicit = builder.build_scopes_result(request=PrepareContextRequest(**settings, evidence_selection=False),
                                          current_scope_id="scope-a", memory_candidates=groups)
    assert ordinary == explicit and not ordinary.fragments
    selected = build(groups, "cobalt", assembly=ContextAssembly(), include_code=True)
    verify(selected, groups, 1400)
    assert selected.fragments


@pytest.mark.parametrize("invalid", [0, 1, "true", None])
def test_evidence_selection_is_a_strict_boolean(invalid):
    with pytest.raises(ValidationError):
        PrepareContextRequest(query="sensor", evidence_selection=invalid)


def test_fragment_coordinates_reject_boolean_float_and_empty_intervals():
    address = build((group(("Sensor evidence.",)),), "sensor").fragments[0].address
    for start, end, text in ((False, 1, "s"), (0, 1.0, "s"), (0, 0, ""), (0, 2, "s")):
        with pytest.raises(PreparedContextInvariantError):
            PreparedEvidenceFragment(address, start, end, text)


async def test_runtime_prepare_and_every_fragment_citation_read_real_memory():
    async with open_builtin_runtime(BuiltinConfig()) as runtime:
        scope = await runtime.scopes.create(ScopeDraft(
            title="Evidence", summary="Exact snippets", idempotency_key="native-selection"
        ))
        memory = runtime.memory.for_scope(scope.scope_id)
        original = (await memory.remember(RememberMemoryRequest(entries=(MemoryEntryInput(
            kind="fact", text="Routine scheduling notes. " * 40 + "Cobalt sensor calibration uses nitrogen at 8 bars."
        ),)))).entry.citation
        result = await runtime.context.for_scope(scope.scope_id).prepare(PrepareContextRequest(
            query="cobalt sensor calibration nitrogen", max_bytes=1400, evidence_selection=True
        ))
        assert result.content_bytes == len(result.content.encode("utf-8")) <= 1400
        items = json.loads(result.content.splitlines()[-2])["items"]
        assert any("8 bars" in item["content"] for item in items)
        for item in items:
            payload = dict(item["citation"])
            assert payload.pop("scope_id") == scope.scope_id
            citation = MemoryCitation.model_validate(payload)
            assert citation.entry_version_id == original.entry_version_id
            entry = await memory.get(GetMemoryEntryRequest(citation=citation))
            assert item["content"] == entry.entry.text[item["start"]:item["end"]]
