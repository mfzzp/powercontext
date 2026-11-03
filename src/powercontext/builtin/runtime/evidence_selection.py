"""Offline, query-driven extraction with exact final-wire budget accounting."""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from powercontext.artifacts import ArtifactAddress
from powercontext.builtin.runtime.models import PrepareContextRequest, PreparedContext
from powercontext.builtin.runtime.prepared_context import (
    MemoryEntryAddress,
    PreparedContextBuild,
    PreparedContextOmissions,
    PreparedEvidenceFragment,
    PreparedMemoryCandidates,
    _BEGIN_MARKER,
    _END_MARKER,
)
from powercontext.builtin.runtime.prepared_text import TRUST_POLICY

_STOP = frozenset(
    "a an the and or of to in on at by for from with without as is are was were be been being "
    "that this these those it its their they we our which who what how can could may might "
    "has have had do does did than then also both either such into through between during "
    "not no more less most least each other some any all using used use study studies results".split()
)
_TOKEN = re.compile(r"[a-z]+|\d+|[\u3400-\u9fff]", re.IGNORECASE)
_BOUNDARY = re.compile(r"(?<=[.!?。！？])\s+|[\r\n]+")


def _terms(text: str) -> tuple[str, ...]:
    terms = []
    for match in _TOKEN.finditer(text.lower()):
        token = match.group()
        if token in _STOP:
            continue
        # Conservative inflection matching; no model or downloaded vocabulary.
        if len(token) > 5 and token.endswith("ies"):
            token = token[:-3] + "y"
        elif len(token) > 5 and token.endswith("ing"):
            token = token[:-3]
            if len(token) > 2 and token[-1] == token[-2]:
                token = token[:-1]
        elif len(token) > 4 and token.endswith("ed"):
            token = token[:-2]
        elif len(token) > 4 and token.endswith("es"):
            token = token[:-2]
        elif len(token) > 3 and token.endswith("s") and not token.endswith(("ss", "us", "is")):
            token = token[:-1]
        if len(token) > 7 and token.endswith("ion"):
            token = token[:-3]
        if len(token) > 4 and token.endswith("e"):
            token = token[:-1]
        terms.append(token)
    return tuple(terms)


@dataclass(frozen=True)
class _Document:
    address: MemoryEntryAddress
    text: str


@dataclass(frozen=True)
class _Sentence:
    document: int
    start: int
    end: int
    terms: tuple[str, ...]


def _intervals(text: str):
    start = 0
    for match in _BOUNDARY.finditer(text):
        if start < match.start():
            yield start, match.start()
        start = match.end()
    if start < len(text):
        yield start, len(text)


def _render(fragments: Sequence[PreparedEvidenceFragment]) -> str:
    items = []
    for fragment in fragments:
        address = fragment.address
        items.append({
            "citation": {
                "scope_id": address.memory.scope_id,
                "memory_ref": address.memory.artifact.model_dump(mode="json"),
                "entry_id": address.entry_id,
                "entry_version_id": address.entry_version_id,
            },
            "content": fragment.text,
            "start": fragment.start,
            "end": fragment.end,
        })
    encoded = json.dumps({"trust": "untrusted_history", "items": items}, ensure_ascii=False, separators=(",", ":"))
    return "\n\n".join((TRUST_POLICY, f"{_BEGIN_MARKER}\n{encoded}\n{_END_MARKER}"))


def _fragments(selected: tuple[int, ...], sentences: Sequence[_Sentence], documents: Sequence[_Document]):
    # Merge adjoining sentences before charging citation overhead. Whitespace
    # between them comes from the original entry, never reconstructed text.
    intervals: list[tuple[int, int, int]] = []
    for index in sorted(selected, key=lambda index: (sentences[index].document, sentences[index].start)):
        sentence = sentences[index]
        if intervals:
            document, start, end = intervals[-1]
            gap = documents[document].text[end:sentence.start] if document == sentence.document else None
            if gap is not None and (sentence.start <= end or not gap.strip()):
                intervals[-1] = document, start, max(end, sentence.end)
                continue
        intervals.append((sentence.document, sentence.start, sentence.end))
    return tuple(
        PreparedEvidenceFragment(documents[document].address, start, end, documents[document].text[start:end])
        for document, start, end in intervals
    )


def _documents(candidates: Sequence[PreparedMemoryCandidates]) -> list[_Document]:
    documents = []
    seen = set()
    for group in candidates:
        for hit in group.hits:
            address = MemoryEntryAddress(
                memory=ArtifactAddress(scope_id=group.scope_id, artifact=hit.memory_ref),
                entry_id=hit.entry_id, entry_version_id=hit.entry_version_id,
            )
            identity = (group.scope_id, hit.memory_ref.family, hit.memory_ref.artifact_id,
                        hit.memory_ref.revision, hit.entry_id, hit.entry_version_id)
            if identity not in seen and hit.text.strip():
                seen.add(identity)
                documents.append(_Document(address, hit.text))
    return documents


def _bounded_windows(document: _Document, start: int, end: int, max_bytes: int):
    words = list(re.finditer(r"\S+", document.text[start:end]))
    boundaries = [(start + word.start(), start + word.end()) for word in words]
    if len(words) <= 1:
        boundaries = [(index, index + 1) for index in range(start, end)]
    position = 0
    while position < len(boundaries):
        lower, upper = position + 1, min(len(boundaries), position + 48)
        best = position
        while lower <= upper:
            middle = (lower + upper) // 2
            left, right = boundaries[position][0], boundaries[middle - 1][1]
            fragment = PreparedEvidenceFragment(document.address, left, right, document.text[left:right])
            if len(_render((fragment,)).encode("utf-8")) <= max_bytes:
                best = middle
                lower = middle + 1
            else:
                upper = middle - 1
        if best > position:
            yield boundaries[position][0], boundaries[best - 1][1]
        position += max(1, (best - position) // 2)


def _sentences(documents: Sequence[_Document], max_bytes: int) -> list[_Sentence]:
    sentences = []
    for number, document in enumerate(documents):
        for start, end in _intervals(document.text):
            while start < end and document.text[start].isspace():
                start += 1
            while end > start and document.text[end - 1].isspace():
                end -= 1
            if start == end:
                continue
            fragment = PreparedEvidenceFragment(document.address, start, end, document.text[start:end])
            if len(_render((fragment,)).encode("utf-8")) <= max_bytes:
                sentences.append(_Sentence(number, start, end, _terms(fragment.text)))
                continue
            # An overlong sentence cannot be sent whole. Offer overlapping
            # word-aligned windows throughout it, including its late evidence.
            for window_start, window_end in _bounded_windows(document, start, end, max_bytes):
                sentences.append(_Sentence(number, window_start, window_end,
                                           _terms(document.text[window_start:window_end])))
    return sentences


def _scores(query_text: str, documents: Sequence[_Document], sentences: Sequence[_Sentence]) -> list[float]:
    query = _terms(query_text)
    query_set = set(query)
    frequencies = Counter(term for sentence in sentences for term in set(sentence.terms))
    weights = {term: 1.0 + math.log(1 + (len(sentences) + 1) / (frequencies[term] + 1)) for term in query_set}
    total_weight = sum(weights.values()) or 1.0
    doc_coverage = [
        sum(weights[term] for term in query_set.intersection(_terms(document.text))) / total_weight
        for document in documents
    ]
    average_length = sum(len(sentence.terms) for sentence in sentences) / max(1, len(sentences))
    scores = []
    bigrams = set(zip(query, query[1:], strict=False))
    for sentence in sentences:
        counts = Counter(sentence.terms)
        coverage = sum(weights[term] for term in query_set.intersection(counts)) / total_weight
        length_norm = 0.5 + 0.5 * len(sentence.terms) / max(1, average_length)
        lexical = sum(weights[term] * counts[term] * 2.2 / (counts[term] + 1.2 * length_norm)
                      for term in query_set.intersection(counts))
        phrase = len(bigrams.intersection(zip(sentence.terms, sentence.terms[1:], strict=False)))
        scores.append((lexical * (0.6 + coverage) + 0.5 * phrase) * (0.7 + 0.6 * doc_coverage[sentence.document]))
    # A neighboring explanation can be evidence even if it refers to the
    # subject by a pronoun. Its score remains subordinate to direct matches.
    direct = scores.copy()
    for index, sentence in enumerate(sentences):
        for neighbor in (index - 1, index + 1):
            if 0 <= neighbor < len(sentences) and sentences[neighbor].document == sentence.document:
                scores[index] += 0.12 * direct[neighbor]
    return scores


def _select_sentences(
    documents: Sequence[_Document], sentences: Sequence[_Sentence], scores: Sequence[float],
    *, max_bytes: int, entry_limit: int,
) -> tuple[int, ...]:
    ranked = sorted((index for index, score in enumerate(scores) if score > 0), key=lambda index: (-scores[index], index))[:48]
    # State: selected intervals, total relevance, exact serialized byte count.
    states = [((), 0.0, 0)]
    for index in ranked:
        options = {state[0]: state for state in states}
        for selected, score, _ in states:
            selection = tuple(sorted((*selected, index)))
            fragments = _fragments(selection, sentences, documents)
            if len(fragments) > entry_limit:
                continue
            size = len(_render(fragments).encode("utf-8"))
            if size <= max_bytes:
                sentence = sentences[index]
                existing = _fragments(selected, sentences, documents)
                covered = sum(
                    max(0, min(fragment.end, sentence.end) - max(fragment.start, sentence.start))
                    for fragment in existing if fragment.address == documents[sentence.document].address
                )
                uncovered = 1 - covered / (sentence.end - sentence.start)
                if uncovered > 0:
                    options[selection] = selection, score + scores[index] * uncovered, size
        values = list(options.values())
        valuable = sorted(values, key=lambda state: (-state[1], state[2], state[0]))[:32]
        efficient = sorted(values, key=lambda state: (-state[1] / max(1, state[2]), state[2], state[0]))[:32]
        states = list({state[0]: state for state in (*valuable, *efficient, ((), 0.0, 0))}.values())
    selected, _, _ = min(states, key=lambda state: (-state[1], state[2], state[0]))
    return selected


def select_memory_evidence(
    request: PrepareContextRequest, candidates: Sequence[PreparedMemoryCandidates], *, entry_limit: int
) -> PreparedContextBuild:
    """Rank literal sentences, then search bounded combinations under wire cost.

    The beam includes short alternatives as well as high-score alternatives;
    citation overhead and adjacency make independent sentence truncation or a
    body-only knapsack inaccurate. Every admitted state is actually serialized.
    """
    documents = _documents(candidates)
    sentences = _sentences(documents, request.max_bytes)
    scores = _scores(request.query, documents, sentences)
    selected = _select_sentences(documents, sentences, scores, max_bytes=request.max_bytes, entry_limit=entry_limit)
    fragments = _fragments(selected, sentences, documents)
    if not fragments:
        return PreparedContextBuild(context=PreparedContext(status="empty", content=None, content_bytes=0), origins=())
    content = _render(fragments)
    partial = sum(
        fragment.start != 0 or fragment.end != len(next(doc.text for doc in documents if doc.address == fragment.address))
        for fragment in fragments
    )
    delivered = {
        (fragment.address.memory.scope_id, fragment.address.memory.artifact.artifact_id,
         fragment.address.memory.artifact.revision, fragment.address.entry_id, fragment.address.entry_version_id)
        for fragment in fragments
    }
    eligible = {sentence.document for sentence, score in zip(sentences, scores, strict=True) if score > 0}
    dropped = max(0, len(eligible) - len(delivered))
    return PreparedContextBuild(
        context=PreparedContext(status="ready", content=content, content_bytes=len(content.encode("utf-8"))),
        origins=tuple(fragment.address for fragment in fragments),
        fragments=fragments,
        omissions=PreparedContextOmissions(
            truncated_items=partial, dropped_items=dropped, dropped_no_fitting_truncation=dropped,
        ),
    )
