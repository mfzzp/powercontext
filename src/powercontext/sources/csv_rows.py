"""Offline CSV row Sources for PowerContext's competition Source contract.

Copy this file into src/powercontext/sources/csv_rows.py in the product checkout.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel

from powercontext.sources.definitions import SourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence


class CSVDocumentInput(BaseModel):
    document_id: str
    primary_key: str
    content: bytes


class CSVRowInput(BaseModel):
    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]


class CSVRowSource(Source):
    materialization: Literal[SourceMaterialization.CAPTURED] = SourceMaterialization.CAPTURED
    source_id: str
    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]


def csv_row_source_id(document_id: str, primary_key_value: str) -> str:
    if not document_id or document_id != document_id.strip():
        raise ValueError("document_id must be non-empty and have no surrounding whitespace")
    if not primary_key_value.strip():
        raise ValueError("primary key value must not be blank")
    try:
        identity = f"csv:{quote(document_id, safe='')}:{quote(primary_key_value, safe='')}"
    except UnicodeError as error:
        raise ValueError("identity components must be valid Unicode") from error
    if len(identity) > 256:
        raise ValueError("CSV row source_id exceeds 256 characters")
    return identity


def _validate_columns(columns: tuple[str, ...], primary_key: str) -> int:
    if not columns or any(not column.strip() for column in columns):
        raise ValueError("CSV header must contain non-blank column names")
    if len(set(columns)) != len(columns):
        raise ValueError("CSV header contains duplicate column names")
    if not primary_key or primary_key not in columns:
        raise ValueError("primary_key must exactly match a header column")
    return columns.index(primary_key)


def _validate_row(value: CSVRowInput) -> None:
    key_index = _validate_columns(value.columns, value.primary_key)
    if len(value.values) != len(value.columns):
        raise ValueError("CSV row width does not match the header")
    if value.values[key_index] != value.primary_key_value:
        raise ValueError("primary_key_value does not match the row")
    csv_row_source_id(value.document_id, value.primary_key_value)


def parse_csv_document(value: CSVDocumentInput) -> tuple[CSVRowInput, ...]:
    if not value.document_id or value.document_id != value.document_id.strip():
        raise ValueError("document_id must be non-empty and have no surrounding whitespace")
    if not value.primary_key:
        raise ValueError("primary_key must not be empty")

    # utf-8-sig accepts one leading BOM and still rejects invalid UTF-8.
    text = value.content.decode("utf-8-sig", errors="strict")
    reader = csv.reader(
        io.StringIO(text, newline=""),
        delimiter=",",
        quotechar='"',
        doublequote=True,
        escapechar=None,
        skipinitialspace=False,
        strict=True,
    )
    rows: list[CSVRowInput] = []
    seen_keys: set[str] = set()
    try:
        header = next(reader, None)
        if header is None:
            raise ValueError("CSV document has no header")
        columns = tuple(header)
        key_index = _validate_columns(columns, value.primary_key)

        for fields in reader:
            # csv.reader returns [] for an empty physical record, but ['']
            # for a quoted empty field. Do not conflate the two.
            if fields == []:
                continue
            if len(fields) != len(columns):
                raise ValueError("CSV row width does not match the header")
            key_value = fields[key_index]
            if not key_value.strip():
                raise ValueError("CSV primary key value must not be blank")
            if key_value in seen_keys:
                raise ValueError("CSV document contains duplicate primary keys")
            csv_row_source_id(value.document_id, key_value)
            seen_keys.add(key_value)
            rows.append(
                CSVRowInput(
                    document_id=value.document_id,
                    primary_key=value.primary_key,
                    primary_key_value=key_value,
                    columns=columns,
                    values=tuple(fields),
                )
            )
    except csv.Error as error:
        raise ValueError(f"Malformed CSV: {error}") from error

    # No partial result is exposed before the entire input has been validated.
    return tuple(rows)


class _CSVRowTextProjection:
    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = CSVRowSource
    output_class = TextEvidence

    def project(self, source: CSVRowSource, /) -> TextEvidence:
        row = dict(zip(source.columns, source.values, strict=True))
        return TextEvidence(
            source_type="csv-row",
            source_id=source.source_id,
            content=json.dumps(row, ensure_ascii=False, separators=(",", ":")),
            metadata={
                "document_id": source.document_id,
                "primary_key": source.primary_key,
                "primary_key_value": source.primary_key_value,
                "columns": list(source.columns),
                "row": row,
            },
        )


class _CSVRowSourceDefinition:
    name = "csv-row"
    version = "1"
    input_class = CSVRowInput
    source_class = CSVRowSource
    projections = (_CSVRowTextProjection(),)

    async def resolve(self, value: CSVRowInput, /) -> CSVRowSource:
        _validate_row(value)
        identity = csv_row_source_id(value.document_id, value.primary_key_value)
        return CSVRowSource(
            name=identity,
            source_id=identity,
            **value.model_dump(),
        )

    async def read(self, source: CSVRowSource, /) -> CSVRowInput:
        value = CSVRowInput(
            document_id=source.document_id,
            primary_key=source.primary_key,
            primary_key_value=source.primary_key_value,
            columns=source.columns,
            values=source.values,
        )
        _validate_row(value)
        identity = csv_row_source_id(value.document_id, value.primary_key_value)
        if source.name != identity or source.source_id != identity:
            raise ValueError("CSV row Source identity does not match its content")
        return value


CSV_ROW_SOURCE_DEFINITION: SourceDefinition[CSVRowInput, CSVRowSource, CSVRowInput] = _CSVRowSourceDefinition()

__all__ = [
    "CSVDocumentInput",
    "CSVRowInput",
    "CSVRowSource",
    "CSV_ROW_SOURCE_DEFINITION",
    "csv_row_source_id",
    "parse_csv_document",
]
