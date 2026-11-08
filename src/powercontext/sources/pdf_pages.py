"""Offline PDF page evidence and lexical retrieval over scoped Memory."""

from __future__ import annotations

import hashlib
import io
import json
import logging
import math
import re
import zlib
from collections import Counter
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel
from pypdf import PdfReader, get_configuration
from pypdf.generic import ArrayObject, EncodedStreamObject, NameObject, NullObject, StreamObject

from powercontext._logging import log_safely
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


def _validate_text_stream(value: Any, checked: set[int]) -> None:
    """Reject damaged compressed text rather than pypdf's partial recovery."""
    if value is None:
        return
    stream = value.get_object()
    if isinstance(stream, NullObject):
        return
    if isinstance(stream, ArrayObject):
        for item in stream:
            _validate_text_stream(item, checked)
        return
    if not isinstance(stream, StreamObject):
        raise ValueError("PDF text content must be a stream")
    if id(stream) in checked:
        return
    checked.add(id(stream))
    filters = stream.get("/Filter")
    if filters is None:
        return
    filters = filters.get_object()
    filters = list(filters) if isinstance(filters, ArrayObject) else [filters]
    for index, filter_name in enumerate(filters):
        if filter_name not in ("/FlateDecode", "/Fl"):
            continue
        data = stream._data
        if index:
            # Decode only the filters preceding Flate, preserving their params.
            prefix = EncodedStreamObject()
            prefix._data = data
            prefix[NameObject("/Filter")] = ArrayObject(filters[:index])
            parameters = stream.get("/DecodeParms")
            if parameters is not None:
                parameters = parameters.get_object()
                prefix[NameObject("/DecodeParms")] = (
                    ArrayObject(parameters[:index]) if isinstance(parameters, ArrayObject) else parameters
                )
            data = prefix.get_data()
        decoder = zlib.decompressobj()
        produced = 0
        maximum = get_configuration().zlib_maximum_output_length
        for offset in range(0, len(data), 65536):
            pending = data[offset:offset + 65536]
            while pending:
                produced += len(decoder.decompress(pending, 65536))
                if maximum and produced > maximum:
                    raise ValueError("PDF text stream exceeds the decoder limit")
                pending = decoder.unconsumed_tail
        if not decoder.eof:
            raise ValueError("PDF text stream is truncated")


def _validate_text_resources(page: Any, checked: set[int]) -> None:
    resources = page.get("/Resources")
    if resources is None:
        return
    resources = resources.get_object()
    if isinstance(resources, NullObject) or id(resources) in checked:
        return
    checked.add(id(resources))
    fonts = resources.get("/Font", {})
    fonts = fonts.get_object() if hasattr(fonts, "get_object") else fonts
    for font in fonts.values():
        _validate_text_stream(font.get_object().get("/ToUnicode"), checked)
    xobjects = resources.get("/XObject", {})
    xobjects = xobjects.get_object() if hasattr(xobjects, "get_object") else xobjects
    for reference in xobjects.values():
        form = reference.get_object()
        if form.get("/Subtype") == "/Form":
            _validate_text_stream(form, checked)
            _validate_text_resources(form, checked)


def parse_pdf_document(value: PDFDocumentInput) -> tuple[PDFPageInput, ...]:
    if not value.document_id.strip():
        raise ValueError("PDF document_id must not be blank")
    if not value.content or b"%PDF-" not in value.content[:1024]:
        raise ValueError("Input is not a PDF document")
    # Even strict pypdf parsing recovers a truncated %%EOF marker and returns
    # pages. Require a complete final revision before accepting any page.
    if not value.content.rstrip(b"\x00\t\n\x0c\r ").endswith(b"%%EOF"):
        raise ValueError("PDF end-of-file marker is missing or truncated")
    digest = hashlib.sha256(value.content).hexdigest()
    try:
        # Strict parsing keeps the contract atomic: a PDF whose cross-reference
        # data needs repair is not completely parseable and must fail instead of
        # yielding a quietly reconstructed page tree.
        reader = PdfReader(io.BytesIO(value.content), strict=True)
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("PDF requires a non-empty password")
        page_count = reader.root_object["/Pages"]["/Count"]
        if not isinstance(page_count, int) or page_count < 0 or len(reader.pages) != page_count:
            raise ValueError("PDF page tree has an inconsistent page count")
        if reader.is_encrypted:
            # pypdf's encrypted page iterator uses the declared count, so
            # check for a hidden extra leaf through the flattened page tree.
            try:
                reader.get_page(page_count)
            except IndexError:
                pass
            else:
                raise ValueError("PDF page tree has an inconsistent page count")
        checked: set[int] = set()
        for page in reader.pages:
            _validate_text_stream(page.get("/Contents"), checked)
            _validate_text_resources(page, checked)
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
_RUNNING_HEAD = re.compile(r"^\s*(?:table\s+of\s+contents)\s*$", re.IGNORECASE)
_STOP_WORDS = frozenset("""
a an and are as at be been being by can could did do does for from had has have how
i if in into is it its may of on or that the their them there these they this those
to was were what when where which who why will with would you your than then also
about after before during each any all such using use used only shown within details
answer respond response give assuming perspective investment analyst question please
fy fiscal year years reported report annual directly outlined explicitly state calculate
define relying following according based information important primarily quantity
company useful metric
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

# Questions that ask for a number are answered on table-heavy pages.
_NUMERIC_INTENT = frozenset(
    "amount quantity percentage rate margin cost revenue income profit price total "
    "value number ebitda capex expense debt cash asset liability".split()
)

# Question intents that the primary statements answer directly.
_METRIC_TERMS = frozenset(
    "income earning profit ebitda margin sale revenue capex expenditure depreciation "
    "amortization cash flow asset liability debt equity".split()
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


def _query_terms(question: str) -> list[str]:
    terms = _terms(question)
    # Recognise the financial phrase before "investment" is removed as prompt
    # boilerplate. Investment in working capital is a different concept.
    capital_phrases = re.sub(r"\bworking\s+capital\s+investments?\b", "", question, flags=re.IGNORECASE)
    if re.search(r"\bcapital\s+investments?\b", capital_phrases, re.IGNORECASE):
        terms.append("capex")
    return terms


def _query_weights(question: str) -> dict[str, float]:
    terms = set(_query_terms(question))
    weights = {
        term: 0.2 if re.fullmatch(r"(?:19|20)\d{2}", term) else 0.7 if term.isdigit() else 1.0
        for term in terms
    }
    for triggers, expansion in _FINANCIAL_CONCEPTS:
        if terms.intersection(_terms(triggers)):
            for term in _terms(expansion):
                weights.setdefault(term, 1.0)
    if "capital" in terms and terms.intersection({"intensive", "intensity"}):
        # A capital-intensity question often needs figures for investment in
        # productive assets, even when the filing never uses that term.
        for term in _terms("capex expenditures purchases additions property plant equipment depreciation"):
            weights.setdefault(term, 1.0)
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


_ALIAS_TOKEN = re.compile(r"\b[A-Z]{3,6}\b")
_TICKER_TOKEN = re.compile(r"\b[A-Z][A-Z0-9]{1,5}\b", re.IGNORECASE)
_TICKER_DECLARATION = re.compile(
    r"\b(?i:trad(?:e|es|ed|ing)\s+under\s+(?:the\s+)?(?:ticker\s+)?symbol"
    r"|(?:trading|ticker)\s+symbol(?:s|\(s\))?[ \t]*:)\s*[\"'“‘]?([A-Z][A-Z0-9]{1,5})\b"
)
_TICKER_LABEL = re.compile(
    r"^\s*(?i:(?:trading|ticker)\s+symbol(?:s|\(s\))?)\s*:?\s*"
    r"[\"'“‘]?([A-Z][A-Z0-9]{1,5})[\"'”’]?\s*$", re.MULTILINE,  # noqa: RUF001 - PDF quotation marks
)
_TICKER_TABLE_HEADING = re.compile(r"\btrading\s+symbol(?:s|\(s\))?", re.IGNORECASE)
_TICKER_TABLE_ROW = re.compile(
    r"\b([A-Z][A-Z0-9]{1,5})\s+(?i:(?:the\s+)?(?:nasdaq\b|nyse\b|"
    r"(?:[a-z]+\s+){1,5}stock\s+(?:exchange|market)\b))"
)


def _disclosed_tickers(body: str) -> set[str]:
    """Read issuer symbols from explicit declarations or SEC cover tables."""
    symbols = set(_TICKER_DECLARATION.findall(body))
    symbols.update(_TICKER_LABEL.findall(body))
    for heading in _TICKER_TABLE_HEADING.finditer(body):
        header = body[max(0, heading.start() - 200):heading.end() + 300]
        if (re.search(r"title\s+of\s+each\s+class", header, re.IGNORECASE) is None
                or re.search(r"name\s+of\s+each\s+exchange", header, re.IGNORECASE) is None):
            continue
        table = body[heading.end():heading.end() + 3000]
        table = re.split(
            r"\b(?:securities\s+registered|indicate\s+by\s+check\s+mark)\b",
            table, maxsplit=1, flags=re.IGNORECASE,
        )[0]
        # A symbol must occupy the column immediately before an exchange name.
        # An unrelated mention of a listed investment is not an issuer alias.
        symbols.update(_TICKER_TABLE_ROW.findall(table))
    return symbols


def _ticker_report(documents: list[str], bodies: list[str], question: str) -> str | None:
    """Route on a symbol disclosed in the supplied reports, when unambiguous."""
    # User spelling need not share the cover table's capitalization. Only a
    # symbol actually disclosed by a single supplied issuer can route a query.
    symbols = {token.upper() for token in _TICKER_TOKEN.findall(question) if token.lower() not in _STOP_WORDS}
    if not symbols:
        return None
    matches = {
        document_id for document_id, body in zip(documents, bodies, strict=True)
        if document_id and symbols.intersection(_disclosed_tickers(body))
    }
    return next(iter(matches)) if len(matches) == 1 else None


def _alias_matches(document_id: str, question: str) -> bool:
    """Recognise an abbreviation that is a subsequence of a document name.

    ``AMEX`` is a subsequence of ``AMERICANEXPRESS`` and ``JPM`` of ``JPMORGAN``,
    so a short upper-case token can identify the report without any alias table.
    """

    words = [word for word in re.findall(r"[a-z]+", document_id.lower()) if len(word) >= 6]
    if not words:
        return False
    for token in _ALIAS_TOKEN.findall(question):
        low = token.lower()
        if low in words:
            continue
        for word in words:
            letters = iter(word)
            if all(letter in letters for letter in low):
                return True
    return False


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


def _opening(body: str) -> str:
    """Drop the repeated running header so a page's own heading and lead text remain."""

    lines = body.lstrip().splitlines()
    while lines and (not lines[0].strip() or _RUNNING_HEAD.match(lines[0])):
        lines.pop(0)
    return "\n".join(lines).lstrip()


def _statement_weight(title: set[str], query: set[str]) -> float:
    """Primary statements answer most numeric questions, so lift them multiplicatively."""

    financial_page = (
        "statement" in title
        or {"balance", "sheet"}.issubset(title)
        or ("cash" in title and "flow" in title)
        or ("operation" in title and "consolidated" in title)
    )
    if not financial_page:
        return 1.0
    if not query.intersection(_METRIC_TERMS):
        return 1.0
    return 1.9


_ROW_VALUE = r"(?:\(?[+−-]?(?:[$€£]\s*)?\d[\d,]*(?:\.\d+)?(?:\s*%)?\)?|[—–-])"
_NUMERIC_ROW = re.compile(rf"^\s*([A-Za-z].{{0,95}}?)\s+{_ROW_VALUE}(?:\s+{_ROW_VALUE})*\s*$")
_EFFECTIVE_TAX_RATE = re.compile(r"\beffective\s+(?:income\s+)?tax\s+rates?\b", re.IGNORECASE)
_ROW_FOOTNOTE = re.compile(r"\((?:[a-z]|\d{1,2})\)|\[(?:[a-z]|\d{1,2})\]|[*†‡]+", re.IGNORECASE)
_MARGIN_NUMERATORS = {
    "operation": {"operation income", "operation profit", "operation earning", "income operation"},
    "gross": {"gross profit", "gross income", "gross earning"},
    "net": {"net income", "net profit", "net earning"},
}
_REVENUE_LABELS = {"revenue", "net revenue", "sale", "net sale"}
_SALES_COST_RATIO = re.compile(
    r"\bcost(?:\s+of)?\s+(?:(?:goods|products)\s+sold|sales|revenue)\b"
    r"[^.;]{0,100}\b(?:percent(?:age)?|proportion|ratio)\s+(?:of|to)\s+"
    r"(?:(?:net|total)\s+)?(?:sales|revenues?)\b", re.IGNORECASE,
)


def _margin_table_strength(body: str, kinds: set[str]) -> int:
    """Recognize numeric margin rows, or the numerator and revenue rows together."""
    # Gross margin is also explained through the complementary cost-to-sales
    # ratio. Filings can discuss its drivers without saying "gross margin".
    if "gross" in kinds and _SALES_COST_RATIO.search(body):
        return 2
    labels = set()
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    for start in range(len(lines)):
        # pypdf may place a label, a wrapped label continuation and its numeric
        # cells on separate lines. Join only a small local window for ranking;
        # the captured page and the text returned through citations stay intact.
        for width in range(1, min(3, len(lines) - start) + 1):
            line = " ".join(lines[start:start + width])
            row = _NUMERIC_ROW.fullmatch(line)
            if row is not None and any(character.isdigit() for character in line[row.end(1):]):
                label = _ROW_FOOTNOTE.sub(" ", row[1])
                labels.add(" ".join(_terms(label)).removeprefix("total "))
    if any(f"{kind} margin" in labels or f"{kind} profit margin" in labels for kind in kinds):
        return 2
    if labels.intersection(_REVENUE_LABELS) and any(labels.intersection(_MARGIN_NUMERATORS[k]) for k in kinds):
        return 1
    return 0


def _effective_tax_rate_table_strength(body: str) -> int:
    for line in body.splitlines():
        if _EFFECTIVE_TAX_RATE.match(line.lstrip()) is None:
            continue
        # PDF extraction can insert spaces before percent signs. A comparison
        # column may also report the change in basis points.
        line = re.sub(r"(?<=\d)[ \t]+%", "%", line)
        line = re.sub(r"\s*(?:bps|basis points)\s*$", "", line, flags=re.IGNORECASE)
        row = _NUMERIC_ROW.fullmatch(line)
        if row is not None and _EFFECTIVE_TAX_RATE.fullmatch(" ".join(_terms(row[1]))):
            if any(character.isdigit() for character in line[row.end(1):]):
                return 1
    return 0


def _inventory_turnover_table_strength(body: str) -> int:
    """Find the two reported operands of inventory turnover, not just its name."""
    title = set(_terms(_opening(body)[:450]))
    balance_sheet = {"balance", "sheet"}.issubset(title)
    income_statement = "statement" in title and bool(title.intersection({"income", "earning", "operation"}))
    if not (balance_sheet or income_statement):
        return 0
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    for start in range(len(lines)):
        for width in range(1, min(3, len(lines) - start) + 1):
            row = _NUMERIC_ROW.fullmatch(" ".join(lines[start:start + width]))
            if row is None or not any(character.isdigit() for character in row[0][row.end(1):]):
                continue
            label = " ".join(_terms(row[1])).removeprefix("total ")
            if balance_sheet and label.split()[:1] == ["inventory"]:
                return 1
            if income_statement and re.match(r"cost (?:good sold|product sold|sale|revenue)\b", label):
                return 1
    return 0


def _fuse_table_evidence(
    scored: list[tuple[bool, float, int]],
    bodies: list[str],
    table_strength: Callable[[str], int],
) -> list[tuple[bool, float, int]]:
    # Rank actual numeric rows separately from prose so a short statement can
    # surface even when many discussion pages repeat the question's wording.
    tables = []
    for lexical_rank, (_, _, index) in enumerate(scored, 1):
        if not _is_contents_page(bodies[index]):
            strength = table_strength(bodies[index])
            if strength:
                tables.append((-strength, lexical_rank, index))
    if not tables:
        return scored
    table_ranks = {index: rank for rank, (_, _, index) in enumerate(sorted(tables), 1)}
    fused = []
    for lexical_rank, (routed, _, index) in enumerate(scored, 1):
        score = 1.0 / (20 + lexical_rank)
        if index in table_ranks:
            score += 0.6 / (20 + table_ranks[index])
        fused.append((routed, score, index))
    return sorted(fused, key=lambda item: (not item[0], -item[1], item[2]))


_PAGE_CACHE: dict[tuple[int, str, int | None], dict[str, tuple[str, int, str]]] = {}


def _projected_page(source: object) -> tuple[str, int, str] | None:
    """Read ``(document_id, page_number, page_text)`` from a captured Source."""

    projection = getattr(source, "projection", None)
    if projection is None:
        return None
    try:
        projected = projection(TEXT_EVIDENCE_PROJECTION_KEY)
    except Exception:  # noqa: BLE001 - Sources without this projection are skipped
        return None
    metadata = projected.get("metadata", {})
    document_id = metadata.get("document_id")
    page_number = metadata.get("page_number")
    if not isinstance(document_id, str) or not isinstance(page_number, int):
        return None
    content = projected.get("content", "")
    _, separator, body = content.partition("\n")
    return document_id, page_number, body if separator else ""


async def _source_pages(
    memory: Any, memory_ref: object = None
) -> dict[str, tuple[str, int, str]]:
    """Map each Source id in this Scope to its exact page identity and full text.

    Memory entries only carry ``SourceRef`` values, so the page identity and the
    whole page text live in the Scope's Source catalog. Reading them there keeps
    ranking independent of how the Memory pipeline splits page text into
    entries. The result is cached per Memory revision: a new revision can only
    follow new sources, so one catalog read serves every question in a run. A
    Memory facade that cannot reach a catalog yields an empty map and the caller
    falls back to the entry text.
    """

    runtime = getattr(memory, "_runtime", None)
    scope_id = getattr(memory, "scope_id", None)
    if runtime is None or scope_id is None:
        return {}
    key = (id(memory), scope_id, getattr(memory_ref, "revision", None))
    cached = _PAGE_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        async with runtime._context(scope_id) as context:
            catalog = context.sources.catalog
            sources = await catalog.list()
            pages: dict[str, tuple[str, int, str]] = {}
            for source in sources:
                page = _projected_page(source)
                if page is not None:
                    pages[catalog.as_ref(source).source_id] = page
    except Exception:  # noqa: BLE001 - catalogue access is an optimisation, never a failure
        return {}
    _PAGE_CACHE.clear()
    _PAGE_CACHE[key] = pages
    return pages


def _rank_pages(
    records: list[MemoryEntryRecord],
    question: str,
    search_order: dict[str, int],
    pages: dict[str, tuple[str, int, str]],
) -> list[int]:
    if not records:
        return []
    documents: list[str] = []
    bodies: list[str] = []
    for record in records:
        page = pages.get(record.entry.sources[0].source_id)
        if page is not None:
            documents.append(page[0])
            bodies.append(page[2])
        else:
            document_id, body = _split_entry(record.entry.text)
            documents.append(document_id)
            bodies.append(body)
    openings = [_opening(body) for body in bodies]
    titles = [set(_terms(opening[:450])) for opening in openings]
    token_lists = [_terms(body) for body in bodies]
    counts = [Counter(tokens) for tokens in token_lists]
    frequency: Counter[str] = Counter()
    for count in counts:
        frequency.update(count.keys())
    average = sum(map(len, token_lists)) / len(records) or 1.0
    weights = _query_weights(question)
    query_terms = set(_query_terms(question))
    report_ids = set(documents)
    exact_matches = {document_id for document_id in report_ids if _document_matches(document_id, question)}
    preferred_report = next(iter(exact_matches)) if len(exact_matches) == 1 else None
    if not exact_matches:
        preferred_report = _ticker_report(documents, bodies, question)
    alias_matches = (
        {document_id for document_id in report_ids if _alias_matches(document_id, question)}
        if not exact_matches and preferred_report is None else set()
    )
    idfs = {term: math.log(1.0 + (len(records) - frequency[term] + 0.5) / (frequency[term] + 0.5))
            for term in weights}
    query_sequence = _terms(question)
    query_pairs = set(zip(query_sequence, query_sequence[1:], strict=False))

    base: list[float] = []
    for index, (record, tokens, count) in enumerate(zip(records, token_lists, counts, strict=True)):
        score = 0.0
        normalization = 1.2 * (0.25 + 0.75 * len(tokens) / average)
        for term, weight in weights.items():
            tf = count[term]
            if tf:
                score += weight * idfs[term] * tf * 2.2 / (tf + normalization)
        # Passage-level scoring: a page is evidence when one dense window answers
        # the question, not because it repeats the words across the whole page.
        best_window = 0.0
        for start in range(0, max(1, len(tokens)), 60):
            window = Counter(tokens[start:start + 120])
            window_score = 0.0
            for term, weight in weights.items():
                tf = window.get(term, 0)
                if tf:
                    window_score += weight * idfs[term] * tf * 2.2 / (tf + 1.2)
            if window_score > best_window:
                best_window = window_score
        pairs = query_pairs.intersection(zip(tokens, tokens[1:], strict=False))
        score += 1.0 * best_window + 0.3 * sum(min(idfs.get(a, 0), idfs.get(b, 0)) for a, b in pairs)
        # A page whose own heading names the question's topic is almost always
        # the evidence page, so this is applied multiplicatively: additive
        # bonuses are noise next to the magnitude of a BM25 score.
        lead = set(_terms(openings[index][:160]))
        lead_hits = [term for term, weight in weights.items()
                     if term in lead and not term.isdigit() and weight >= 1.0]
        score *= 1.0 + 0.55 * min(3, len(lead_hits))
        # A statement or table page is mostly numbers, while a narrative page
        # is not; silence here let prose pages outrank the actual tables. Only
        # questions that ask for a figure get this signal.
        if query_terms.intersection(_NUMERIC_INTENT):
            numeric_ratio = sum(1 for token in tokens if token.isdigit()) / max(1, len(tokens))
            score *= 1.0 + 1.4 * min(numeric_ratio, 0.35) / 0.35
        base.append(score)

    scored = []
    for index, score in enumerate(base):
        title = titles[index]
        score *= _statement_weight(title, query_terms)
        score += _heading_bonus(title, query_terms)
        if _is_contents_page(bodies[index]):
            score *= 0.75
        # Subsequence abbreviations are weaker than complete company names.
        if len(alias_matches) == 1 and documents[index] in alias_matches:
            score *= 3.0
        if _report_year_matches(documents[index], question):
            score *= 1.35
        rank = search_order.get(records[index].entry.entry_id)
        if rank is not None:
            score += 0.05 / (1 + rank)
        # Reserve the front of the result for a uniquely named report while
        # keeping other reports available when it has fewer matching pages.
        routed = documents[index] == preferred_report and score > 0.0
        scored.append((routed, score, index))
    scored.sort(key=lambda item: (not item[0], -item[1], item[2]))
    margin_kinds = query_terms.intersection(_MARGIN_NUMERATORS)
    if "margin" in query_terms and margin_kinds:
        scored = _fuse_table_evidence(scored, bodies, lambda body: _margin_table_strength(body, margin_kinds))
    if _EFFECTIVE_TAX_RATE.search(question):
        scored = _fuse_table_evidence(scored, bodies, _effective_tax_rate_table_strength)
    if {"inventory", "turnover"}.issubset(query_terms):
        scored = _fuse_table_evidence(scored, bodies, _inventory_turnover_table_strength)
    return [index for _, _, index in scored]


async def search_pdf_pages(memory: Any, question: str, limit: int = 10) -> tuple[MemoryCitation, ...]:
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
        raise ValueError("limit must be a non-negative integer")
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must not be blank")
    if limit == 0:
        return ()

    from powercontext.builtin.runtime import GetMemoryEntryRequest, SearchMemoryRequest

    # Force offline lexical search, even if the deployment has model credentials.
    request = SearchMemoryRequest(
        query=_retrieval_query(question), limit=min(100, max(20, limit * 4)), mode="fts"
    )
    search_order: dict[str, int] = {}
    try:
        hits = await memory.search(request)
    except Exception as error:  # noqa: BLE001 - FTS supplies only an optional ranking hint
        log_safely(
            logging.getLogger(__name__),
            logging.WARNING,
            "PDF FTS hint unavailable; ranking scoped Memory records",
            extra={"event": "pdf.search.fts_fallback", "error_type": type(error).__name__},
        )
    else:
        search_order = {hit.entry_id: rank for rank, hit in enumerate(hits.hits)}
    # Public scoped listing supplies corpus statistics and avoids dropping a page
    # solely because a long natural-language query fails FTS admission thresholds.
    # All candidates and all references remain real active Memory records.
    page = await memory.list()
    records = [
        record for record in page.entries
        if len(record.entry.sources) == 1 and record.entry.sources[0].source_type == "pdf-page"
    ]
    # Rank full Source pages when available. The exact identity comes from the
    # entry's SourceRef and includes the revision digest.
    pages = await _source_pages(memory, getattr(page, "memory_ref", None))
    result: list[MemoryCitation] = []
    seen: set[str] = set()
    for index in _rank_pages(records, question, search_order, pages):
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
