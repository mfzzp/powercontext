"""Offline PDF page evidence and lexical retrieval over scoped Memory."""

from __future__ import annotations

import hashlib
import io
import json
import math
import re
from collections import Counter
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel
from pypdf import PdfReader

from powercontext.artifacts import MemoryCitation
from powercontext.sources.definitions import SourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

if TYPE_CHECKING:
    from powercontext.builtin.runtime import MemoryEntryRecord


class PDFDocumentInput(BaseModel):
    document_id: str
    content: bytes


class PDFPageInput(BaseModel):
    document_id: str
    document_sha256: str
    page_number: int
    text: str


class PDFPageSource(Source):
    materialization: Literal[SourceMaterialization.CAPTURED] = SourceMaterialization.CAPTURED
    document_id: str
    document_sha256: str
    page_number: int
    text: str


def _validate_page(value: PDFPageInput) -> None:
    if not value.document_id.strip():
        raise ValueError("PDF document_id must not be blank")
    if re.fullmatch(r"[0-9a-f]{64}", value.document_sha256) is None:
        raise ValueError("PDF digest must be a lowercase SHA-256 hexadecimal string")
    if value.page_number < 1:
        raise ValueError("PDF physical page numbers start at 1")


def _page_identity(value: PDFPageInput) -> str:
    encoded = json.dumps(
        [value.document_id, value.document_sha256, value.page_number],
        ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")
    return f"pdf:{hashlib.sha256(encoded).hexdigest()}"


def parse_pdf_document(value: PDFDocumentInput) -> tuple[PDFPageInput, ...]:
    if not value.document_id.strip():
        raise ValueError("PDF document_id must not be blank")
    if not value.content or b"%PDF-" not in value.content[:1024]:
        raise ValueError("Input is not a PDF document")
    digest = hashlib.sha256(value.content).hexdigest()
    try:
        reader = PdfReader(io.BytesIO(value.content), strict=True)
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("PDF requires a non-empty password")
        # Materialize all pages and all text before exposing any result.
        return tuple(
            PDFPageInput(
                document_id=value.document_id,
                document_sha256=digest,
                page_number=number,
                text=page.extract_text() or "",
            )
            for number, page in enumerate(reader.pages, start=1)
        )
    except Exception as error:
        # pypdf exposes several exception types for malformed object streams,
        # encryption and incomplete page trees; the public contract is ValueError.
        raise ValueError("PDF could not be completely parsed") from error


class _PDFPageTextProjection:
    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PDFPageSource
    output_class = TextEvidence

    def project(self, source: PDFPageSource, /) -> TextEvidence:
        return TextEvidence(
            source_type="pdf-page",
            source_id=source.name,
            content=f"{source.document_id}\n{source.text}",
            metadata={
                "document_id": source.document_id,
                "document_sha256": source.document_sha256,
                "page_number": source.page_number,
            },
        )


class _PDFPageSourceDefinition:
    name = "pdf-page"
    version = "1"
    input_class = PDFPageInput
    source_class = PDFPageSource
    projections = (_PDFPageTextProjection(),)

    async def resolve(self, value: PDFPageInput, /) -> PDFPageSource:
        _validate_page(value)
        return PDFPageSource(name=_page_identity(value), **value.model_dump())

    async def read(self, source: PDFPageSource, /) -> PDFPageInput:
        value = PDFPageInput.model_validate(source.model_dump())
        _validate_page(value)
        if source.name != _page_identity(value):
            raise ValueError("PDF Source identity does not match its page")
        return value


PDF_PAGE_SOURCE_DEFINITION: SourceDefinition[PDFPageInput, PDFPageSource, PDFPageInput] = _PDFPageSourceDefinition()


_TOKEN = re.compile(r"[a-z]+|\d+")
_STOP_WORDS = frozenset("""
a an and are as at be been being by can could did do does for from had has have how
i if in into is it its may of on or that the their them there these they this those
to was were what when where which who why will with would you your than then also
about after before during each any all such using use used only shown within details
answer respond response give assuming perspective investment analyst question please
fy fiscal year years reported report annual directly outlined explicitly state calculate
define relying following according based information important primarily quantity
""".split())

# General financial vocabulary, independent of documents, questions and annotations.
_FINANCIAL_CONCEPTS = (
    ("geography geographic geographical geographies regions countries international markets",
     "geographic geography region country international market segment operation"),
    ("legal litigation lawsuit lawsuits battles", "legal litigation lawsuit proceeding contingency settlement"),
    ("restructuring reorganization", "restructuring reorganization charge cost impairment"),
    ("ebitda", "operating income earning depreciation amortization"),
    ("capex expenditures expenditure", "capital expenditure purchase property plant equipment"),
    ("revenue revenues sales", "revenue sale net"),
    ("margin", "income revenue sale net operating"),
    ("debt borrowings indebtedness", "debt borrowing indebtedness obligation"),
    ("dividend dividends", "dividend distribution shareholder"),
    ("income earnings profit", "income earning profit operation"),
    ("depreciation amortization", "depreciation amortization"),
)


def _stem(term: str) -> str:
    if term in {"operates", "operating", "operations", "operation", "operational"}:
        return "operation"
    if term in {"geographies", "geographic", "geographical", "geography"}:
        return "geography"
    if len(term) > 4 and term.endswith("ies"):
        return term[:-3] + "y"
    if len(term) > 4 and term.endswith("s") and not term.endswith(("ss", "us", "is")):
        return term[:-1]
    return term


def _terms(text: str) -> list[str]:
    return [_stem(term) for term in _TOKEN.findall(text.lower()) if term not in _STOP_WORDS]


def _query_weights(question: str) -> dict[str, float]:
    terms = set(_terms(question))
    weights = {term: 0.25 if term.isdigit() else 1.0 for term in terms}
    for triggers, expansion in _FINANCIAL_CONCEPTS:
        if terms.intersection(_terms(triggers)):
            for term in _terms(expansion):
                weights.setdefault(term, 0.3)
    return weights


def _document_matches(document_id: str, question: str) -> bool:
    # IDs commonly combine the company and report year/type. Infer entity hints
    # from the supplied ID, without a company list or a document lookup table.
    parts = [
        part for part in _TOKEN.findall(document_id.lower())
        if part.isalpha() and len(part) > 2 and part not in _STOP_WORDS
    ]
    compact_question = "".join(_TOKEN.findall(question.lower()))
    return any(part in compact_question for part in parts)


def _heading_bonus(title: set[str], query: set[str]) -> float:
    """Favor primary financial statements when a question needs their figures."""
    score = 0.0
    if "statement" in title and title.intersection({"income", "earning", "operation"}):
        if query.intersection({"income", "earning", "profit", "ebitda", "margin", "sale", "revenue"}):
            score += 3.0
    if {"cash", "flow"}.issubset(title):
        if query.intersection({"cash", "flow", "capex", "expenditure", "depreciation", "amortization", "ebitda"}):
            score += 3.0
    if {"balance", "sheet"}.issubset(title):
        if query.intersection({"balance", "asset", "liability", "liquidity", "working"}):
            score += 3.0
    if title.intersection({"litigation", "proceeding", "contingency"}) and query.intersection({"legal", "litigation", "lawsuit"}):
        score += 2.0
    return score


def _rank_pages(records: list[MemoryEntryRecord], question: str, search_order: dict[str, int]) -> list[int]:
    if not records:
        return []
    token_lists = [_terms(record.entry.text) for record in records]
    counts = [Counter(tokens) for tokens in token_lists]
    frequency: Counter[str] = Counter()
    for count in counts:
        frequency.update(count.keys())
    average = sum(map(len, token_lists)) / len(records) or 1.0
    weights = _query_weights(question)
    query_terms = set(_terms(question))
    doc_matches = [_document_matches(record.entry.text.partition("\n")[0], question) for record in records]
    any_doc_match = any(doc_matches)
    scored = []
    for index, (record, tokens, count) in enumerate(zip(records, token_lists, counts, strict=True)):
        score = 0.0
        normalization = 1.2 * (0.25 + 0.75 * len(tokens) / average)
        for term, weight in weights.items():
            tf = count[term]
            if tf:
                df = frequency[term]
                idf = math.log(1.0 + (len(records) - df + 0.5) / (df + 0.5))
                score += weight * idf * tf * 2.2 / (tf + normalization)
        title = set(_terms(record.entry.text[:450]))
        score += 0.15 * sum(weight for term, weight in weights.items() if term in title and not term.isdigit())
        score += _heading_bonus(title, query_terms)
        lower = record.entry.text.lower()
        if "table of contents" in lower[:800]:
            score *= 0.65
        if any_doc_match and not doc_matches[index]:
            score *= 0.05
        rank = search_order.get(record.entry.entry_id)
        if rank is not None:
            score += 0.05 / (1 + rank)
        scored.append((score, index))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [index for _, index in scored]


async def search_pdf_pages(memory: Any, question: str, limit: int = 10) -> tuple[MemoryCitation, ...]:
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
        raise ValueError("limit must be a non-negative integer")
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must not be blank")
    if limit == 0:
        return ()

    from powercontext.builtin.runtime import GetMemoryEntryRequest, SearchMemoryRequest

    # Force offline lexical search, even if the deployment has model credentials.
    hits = await memory.search(SearchMemoryRequest(query=question, limit=max(80, limit * 4), mode="fts"))
    search_order = {hit.entry_id: rank for rank, hit in enumerate(hits.hits)}
    # Public scoped listing supplies corpus statistics and avoids dropping a page
    # solely because a long natural-language query fails FTS admission thresholds.
    # All candidates and all references remain real active Memory records.
    page = await memory.list()
    records = [
        record for record in page.entries
        if len(record.entry.sources) == 1 and record.entry.sources[0].source_type == "pdf-page"
    ]
    result: list[MemoryCitation] = []
    seen: set[str] = set()
    for index in _rank_pages(records, question, search_order):
        record = records[index]
        source_id = record.entry.sources[0].source_id
        if source_id in seen:
            continue
        # Resolve the exact version through the same scoped public API before
        # returning its citation; never synthesize an entry or a revision.
        exact = await memory.get(GetMemoryEntryRequest(citation=record.citation))
        if exact.entry.text != record.entry.text or exact.entry.sources != record.entry.sources:
            raise ValueError("PDF Memory citation did not resolve to its exact page")
        result.append(record.citation)
        seen.add(source_id)
        if len(result) == limit:
            break
    return tuple(result)


__all__ = [
    "PDF_PAGE_SOURCE_DEFINITION",
    "PDFDocumentInput",
    "PDFPageInput",
    "PDFPageSource",
    "parse_pdf_document",
    "search_pdf_pages",
]
