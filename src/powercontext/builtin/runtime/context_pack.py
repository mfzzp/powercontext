"""Canonical UTF-8 context packs and independently reconstructible prefix deltas."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from dataclasses import dataclass

_PACK_SCHEMA = "powercontext.context-pack.v1"
_DELTA_SCHEMA = "powercontext.context-pack-delta.v1"


class ContextPackError(ValueError):
    """A context pack violates the wire format or reconstruction contract."""


def _encode(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ContextPackError("duplicate-field")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ContextPackError(f"invalid-json-number:{value}")


def _decode(raw: bytes, fields: set[str], schema: str) -> dict[str, object]:
    if not isinstance(raw, bytes):
        raise ContextPackError("wire-must-be-bytes")
    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except (ValueError, RecursionError) as error:
        raise ContextPackError("invalid-json") from error
    if not isinstance(payload, dict) or set(payload) != fields or payload.get("schema") != schema:
        raise ContextPackError("invalid-fields-or-schema")
    return payload


def _validate_digest(value: object) -> None:
    if not isinstance(value, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
        raise ContextPackError("invalid-digest")


def _validate_items(items: object) -> None:
    if not isinstance(items, tuple):
        raise ContextPackError("items-must-be-a-tuple")
    for item in items:
        if not isinstance(item, bytes):
            raise ContextPackError("item-must-be-bytes")
        try:
            item.decode("utf-8")
        except UnicodeError as error:
            raise ContextPackError("item-is-not-utf8") from error


def _wire_items(items: tuple[bytes, ...]) -> list[str]:
    return [base64.b64encode(item).decode("ascii") for item in items]


def _read_items(value: object) -> tuple[bytes, ...]:
    if not isinstance(value, list):
        raise ContextPackError("wire-items-must-be-an-array")
    items = []
    for encoded in value:
        if not isinstance(encoded, str):
            raise ContextPackError("wire-item-must-be-a-string")
        try:
            raw = base64.b64decode(encoded.encode("ascii"), validate=True)
        except (UnicodeError, binascii.Error) as error:
            raise ContextPackError("invalid-base64") from error
        if base64.b64encode(raw).decode("ascii") != encoded:
            raise ContextPackError("noncanonical-base64")
        items.append(raw)
    result = tuple(items)
    _validate_items(result)
    return result


def _pack_fields(items: tuple[bytes, ...]) -> tuple[str, int]:
    unsigned: dict[str, object] = {"schema": _PACK_SCHEMA, "items": _wire_items(items)}
    digest = "sha256:" + hashlib.sha256(_encode(unsigned)).hexdigest()
    count = 0
    while True:
        actual = len(_encode({**unsigned, "digest": digest, "byte_count": count}))
        if actual == count:
            return digest, count
        count = actual


@dataclass(frozen=True, slots=True)
class ContextPack:
    items: tuple[bytes, ...]
    digest: str
    byte_count: int

    def __post_init__(self) -> None:
        _validate_items(self.items)
        _validate_digest(self.digest)
        if type(self.byte_count) is not int or self.byte_count < 0:
            raise ContextPackError("invalid-byte-count")
        if (self.digest, self.byte_count) != _pack_fields(self.items):
            raise ContextPackError("pack-integrity-mismatch")

    @classmethod
    def build(cls, items: tuple[bytes, ...]) -> ContextPack:
        _validate_items(items)
        digest, count = _pack_fields(items)
        return cls(items, digest, count)

    @classmethod
    def from_content(cls, content: str | bytes | None, *, chunk_bytes: int = 512) -> ContextPack:
        if type(chunk_bytes) is not int or chunk_bytes < 1:
            raise ContextPackError("invalid-chunk-limit")
        try:
            if content is None:
                raw = b""
            elif isinstance(content, str):
                raw = content.encode("utf-8")
            elif isinstance(content, bytes):
                raw = content
                raw.decode("utf-8")
            else:
                raise ContextPackError("invalid-content-type")
        except UnicodeError as error:
            raise ContextPackError("content-is-not-utf8") from error
        items = []
        start = 0
        while start < len(raw):
            end = min(start + chunk_bytes, len(raw))
            if end < len(raw):
                # Move to the leading byte of the next complete UTF-8 character.
                while end > start and raw[end] & 0xC0 == 0x80:
                    end -= 1
            if end == start:
                raise ContextPackError("character-exceeds-chunk-limit")
            items.append(raw[start:end])
            start = end
        return cls.build(tuple(items))

    def to_bytes(self) -> bytes:
        return _encode({
            "schema": _PACK_SCHEMA,
            "items": _wire_items(self.items),
            "digest": self.digest,
            "byte_count": self.byte_count,
        })

    @classmethod
    def from_bytes(cls, raw: bytes) -> ContextPack:
        payload = _decode(raw, {"schema", "items", "digest", "byte_count"}, _PACK_SCHEMA)
        return cls(_read_items(payload["items"]), payload["digest"], payload["byte_count"])


@dataclass(frozen=True, slots=True)
class ContextPackDelta:
    base_digest: str
    target_digest: str
    prefix_items: int
    appended: tuple[bytes, ...]

    def __post_init__(self) -> None:
        _validate_digest(self.base_digest)
        _validate_digest(self.target_digest)
        if type(self.prefix_items) is not int or self.prefix_items < 0:
            raise ContextPackError("invalid-prefix")
        _validate_items(self.appended)

    @classmethod
    def between(cls, base: ContextPack, target: ContextPack) -> ContextPackDelta:
        if not isinstance(base, ContextPack) or not isinstance(target, ContextPack):
            raise ContextPackError("invalid-pack-type")
        prefix = 0
        for old, new in zip(base.items, target.items):
            if old != new:
                break
            prefix += 1
        return cls(base.digest, target.digest, prefix, target.items[prefix:])

    @property
    def byte_count(self) -> int:
        return len(self.to_bytes())

    def to_bytes(self) -> bytes:
        return _encode({
            "schema": _DELTA_SCHEMA,
            "base_digest": self.base_digest,
            "target_digest": self.target_digest,
            "prefix_items": self.prefix_items,
            "appended": _wire_items(self.appended),
        })

    @classmethod
    def from_bytes(cls, raw: bytes) -> ContextPackDelta:
        payload = _decode(
            raw, {"schema", "base_digest", "target_digest", "prefix_items", "appended"}, _DELTA_SCHEMA
        )
        return cls(
            payload["base_digest"], payload["target_digest"], payload["prefix_items"], _read_items(payload["appended"])
        )


def apply_context_pack_delta(base: ContextPack, delta: ContextPackDelta) -> ContextPack:
    if not isinstance(base, ContextPack) or not isinstance(delta, ContextPackDelta):
        raise ContextPackError("invalid-pack-or-delta-type")
    if base.digest != delta.base_digest:
        raise ContextPackError("base-digest-mismatch")
    if delta.prefix_items > len(base.items):
        raise ContextPackError("prefix-out-of-bounds")
    target = ContextPack.build(base.items[:delta.prefix_items] + delta.appended)
    if target.digest != delta.target_digest:
        raise ContextPackError("target-digest-mismatch")
    return target


__all__ = ["ContextPack", "ContextPackDelta", "ContextPackError", "apply_context_pack_delta"]
