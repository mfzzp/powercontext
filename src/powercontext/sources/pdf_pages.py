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
    if type(value.page_number) is not int or value.page_number < 1:
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
        # Non-strict mode lets pypdf repair the minor cross-reference damage
        # that real annual reports routinely carry; genuinely broken inputs
        # still raise below, so the atomic-failure contract is unchanged.
        reader = PdfReader(io.BytesIO(value.content))
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
    except ValueError:
        raise
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
_TOC_ENTRY = re.compile(r"\s*(?:item\s+\d|part\s+[ivx])")
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
    ("buyback buybacks repurchase repurchases", "repurchase treasury stock share"),
    ("liquidity", "current asset liability cash liquidity"),
    ("inventory inventories", "inventory inventories"),
    ("receivable receivables", "receivable account allowance"),
    ("pension retirement", "pension retirement benefit obligation"),
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
    weights = {
        term: 0.2 if re.fullmatch(r"(?:19|20)\d{2}", term) else 0.7 if term.isdigit() else 1.0
        for term in terms
    }
    for triggers, expansion in _FINANCIAL_CONCEPTS:
        if terms.intersection(_terms(triggers)):
            for term in _terms(expansion):
                weights.setdefault(term, 0.3)
    return weights


_CORPORATE_WORDS = frozenset("inc incorporated corp corporation company co ltd limited plc llc holdings group".split())


def _document_matches(document_id: str, question: str) -> bool:
    # Infer an entity only from the supplied ID, with word boundaries. In
    # particular, INC must not match income, nor AIR match impairment.
    company = re.sub(r"(?:19|20)\d{2}|10[\W_]*[kq](?![a-z0-9])", " ", document_id.lower())
    parts = [part for part in re.findall(r"\d+[a-z]+|[a-z]+", company)
             if part not in _STOP_WORDS and part not in _CORPORATE_WORDS]
    if not parts:
        return False
    pattern = r"(?<![a-z0-9])" + r"[\W_]*".join(map(re.escape, parts)) + r"(?![a-z0-9])"
    return re.search(pattern, question, flags=re.IGNORECASE) is not None


def _report_year_matches(document_id: str, question: str) -> bool:
    years = set(re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", question))
    return bool(years.intersection(re.findall(r"(?:19|20)\d{2}", document_id)))


def _retrieval_query(question: str) -> str:
    # Long questions contain instructions as well as search terms. Keep their
    # original word forms for the Memory analyzer; local stemming belongs only
    # to the separate page reranker.
    terms = list(dict.fromkeys(term for term in _TOKEN.findall(question.lower()) if term not in _STOP_WORDS))
    return " ".join(terms[:24]) if terms else question


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


def _split_entry(text: str) -> tuple[str, str]:
    """Recover the ``DOCUMENT_ID\\nPAGE_TEXT`` projection shape defensively.

    Only a short, space-free first line is treated as a document identifier, so
    a Memory pipeline that stores or chunks page text differently degrades to
    body-only ranking instead of misreading a sentence as a document id.
    """
    head, separator, body = text.partition("\n")
    head = head.strip()
    if separator and 0 < len(head) <= 128 and " " not in head:
        return head, body
    return "", text


def _is_contents_page(body: str) -> bool:
    """Identify a literal table-of-contents page rather than any page with that header."""
    return sum(1 for line in body[:1500].lower().splitlines() if _TOC_ENTRY.match(line)) >= 8


def _projected_metadata(source: object) -> dict[str, Any] | None:
    """Read the page metadata a captured Source projected for transport."""

    projection = getattr(source, "projection", None)
    if projection is None:
        return None
    try:
        metadata = projection(TEXT_EVIDENCE_PROJECTION_KEY).get("metadata", {})
    except Exception:  # noqa: BLE001 - Sources without this projection are skipped
        return None
    if not isinstance(metadata.get("document_id"), str) or not isinstance(metadata.get("page_number"), int):
        return None
    return metadata


async def _source_page_metadata(memory: Any) -> dict[str, tuple[str, int]]:
    """Map each Source id in this Scope to its exact ``(document_id, page_number)``.

    Memory entries only carry ``SourceRef`` values, so page identity lives in the
    Scope's Source catalog. Read it through the Runtime that owns this Memory. A
    Memory facade that cannot reach one yields an empty map, and the caller falls
    back to reading the identity out of the projection text.
    """

    runtime = getattr(memory, "_runtime", None)
    scope_id = getattr(memory, "scope_id", None)
    if runtime is None or scope_id is None:
        return {}
    try:
        async with runtime._context(scope_id) as context:
            catalog = context.sources.catalog
            sources = await catalog.list()
            pages: dict[str, tuple[str, int]] = {}
            for source in sources:
                metadata = _projected_metadata(source)
                if metadata is not None:
                    pages[catalog.as_ref(source).source_id] = (
                        metadata["document_id"],
                        metadata["page_number"],
                    )
            return pages
    except Exception:  # noqa: BLE001 - metadata is an optimisation, never a failure
        return {}


def _rank_pages(records: list[MemoryEntryRecord], question: str, search_order: dict[str, int]) -> list[int]:
    if not records:
        return []
    splits = [_split_entry(record.entry.text) for record in records]
    documents = [document_id for document_id, _ in splits]
    bodies = [body for _, body in splits]
    token_lists = [_terms(body) for body in bodies]
    counts = [Counter(tokens) for tokens in token_lists]
    frequency: Counter[str] = Counter()
    for count in counts:
        frequency.update(count.keys())
    average = sum(map(len, token_lists)) / len(records) or 1.0
    weights = _query_weights(question)
    query_terms = set(_terms(question))
    doc_matches = [_document_matches(document_id, question) for document_id in documents]
    # Only an unambiguous mention should steer the ranking toward one report.
    matched_documents = sum(doc_matches)
    idfs = {term: math.log(1.0 + (len(records) - frequency[term] + 0.5) / (frequency[term] + 0.5))
            for term in weights}
    query_sequence = _terms(question)
    query_pairs = set(zip(query_sequence, query_sequence[1:], strict=False))
    scored = []
    for index, (record, tokens, count) in enumerate(zip(records, token_lists, counts, strict=True)):
        score = 0.0
        normalization = 1.2 * (0.25 + 0.75 * len(tokens) / average)
        for term, weight in weights.items():
            tf = count[term]
            if tf:
                score += weight * idfs[term] * tf * 2.2 / (tf + normalization)
        # Evidence density and literal phrases complement whole-page BM25,
        # especially when repeated generic financial words dominate long pages.
        windows = (set(tokens[start:start + 120]) for start in range(0, len(tokens), 60))
        proximity = max((sum(weights[term] * idfs[term] for term in window.intersection(weights))
                         for window in windows), default=0.0)
        pairs = query_pairs.intersection(zip(tokens, tokens[1:], strict=False))
        score += 0.3 * proximity + 0.3 * sum(min(idfs.get(a, 0), idfs.get(b, 0)) for a, b in pairs)
        title = set(_terms(bodies[index][:450]))
        score += 0.15 * sum(weight for term, weight in weights.items() if term in title and not term.isdigit())
        score += _heading_bonus(title, query_terms)
        if _is_contents_page(bodies[index]):
            score *= 0.75
        # Positive, bounded priors instead of eliminating every other report
        # after one possibly ambiguous company-name match.
        if doc_matches[index] and matched_documents == 1:
            score *= 1.6
        if _report_year_matches(documents[index], question):
            score *= 1.25
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
    hits = await memory.search(SearchMemoryRequest(query=_retrieval_query(question), limit=min(100, max(20, limit * 4)), mode="fts"))
    search_order = {hit.entry_id: rank for rank, hit in enumerate(hits.hits)}
    # Public scoped listing supplies corpus statistics and avoids dropping a page
    # solely because a long natural-language query fails FTS admission thresholds.
    # All candidates and all references remain real active Memory records.
    page = await memory.list()
    records = [
        record for record in page.entries
        if len(record.entry.sources) == 1 and record.entry.sources[0].source_type == "pdf-page"
    ]
    # Exact page identity comes from the Source catalog; the projection text is
    # only a fallback for a Memory facade that cannot reach it.
    pages = await _source_page_metadata(memory)
    result: list[MemoryCitation] = []
    seen: set[tuple[object, object]] = set()
    for index in _rank_pages(records, question, search_order):
        record = records[index]
        source_id = record.entry.sources[0].source_id
        document_id, body = _split_entry(record.entry.text)
        page_key: tuple[object, object] = pages.get(source_id) or (
            document_id,
            " ".join(body.split()).casefold(),
        )
        if page_key in seen:
            continue
        # Resolve the exact version through the same scoped public API before
        # returning its citation; never synthesize an entry or a revision.
        exact = await memory.get(GetMemoryEntryRequest(citation=record.citation))
        if exact.entry.text != record.entry.text or exact.entry.sources != record.entry.sources:
            raise ValueError("PDF Memory citation did not resolve to its exact page")
        result.append(record.citation)
        seen.add(page_key)
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
