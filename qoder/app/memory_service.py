"""PowerContext 记忆服务封装：scope 管理、写入、读取、推荐。"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    GetMemoryEntryRequest,
    RememberMemoryRequest,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft

from .questions import BY_ID, QUESTIONS, TOPICS

logger = logging.getLogger("review-station.memory")

ARCHIVE_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
REQUEST_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")
ENTRY_KIND = "review-practice"


def compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def validate_score(raw: Any) -> int:
    if not isinstance(raw, int) or isinstance(raw, bool):
        raise ValueError("score 必须是整数 0/1/2")
    if raw not in (0, 1, 2):
        raise ValueError("score 必须是 0（不会）、1（模糊）或 2（掌握）")
    return raw


def validate_archive(code: str) -> str:
    if not isinstance(code, str) or not ARCHIVE_RE.fullmatch(code):
        raise ValueError("存档码仅支持 1-64 位字母、数字、下划线和连字符")
    return code


def validate_request_id(rid: str) -> str:
    if not isinstance(rid, str) or not REQUEST_ID_RE.fullmatch(rid):
        raise ValueError("request_id 仅支持 1-128 位字母、数字、下划线和连字符")
    return rid


def validate_topic(topic: str) -> str:
    if topic not in TOPICS:
        raise ValueError(f"无效主题：{topic}")
    return topic


def validate_qid(qid: str) -> str:
    if qid not in BY_ID:
        raise ValueError(f"无效题目 ID：{qid}")
    return qid


@dataclass
class PracticeRecord:
    request_id: str
    qid: str
    score: int
    sequence: int
    timestamp: str
    citation: dict


class MemoryService:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir.resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.config = BuiltinConfig(
            database=SQLiteConfig(
                url="sqlite+aiosqlite:///"
                + (self.data_dir / "review-memory.db").as_posix()
            )
        )
        self._write_lock = Lock()

    def _scope_key(self, archive_code: str) -> str:
        return "review-station/" + archive_code

    async def _get_memory_and_scope(self, runtime, archive_code: str):
        scope = await runtime.scopes.create(
            ScopeDraft(
                title="复习接力站",
                summary="学习者复习记录：" + archive_code,
                idempotency_key=self._scope_key(archive_code),
            )
        )
        return runtime.memory.for_scope(scope.scope_id), scope.scope_id

    async def _read_records(self, memory) -> list[tuple[dict, dict]]:
        page = await memory.list()
        results = []
        for record in page.entries:
            if record.entry.kind == ENTRY_KIND:
                exact = await memory.get(
                    GetMemoryEntryRequest(citation=record.citation)
                )
                data = json.loads(exact.entry.text)
                citation = record.citation.model_dump(mode="json")
                results.append((data, citation))
        results.sort(key=lambda pair: pair[0]["sequence"])
        return results

    def _audit(self, operation: str, **fields: Any) -> None:
        record = {
            "time": datetime.now(timezone.utc).isoformat(),
            "operation": operation,
            **fields,
        }
        with (self.data_dir / "powercontext-audit.jsonl").open(
            "a", encoding="utf-8"
        ) as f:
            f.write(compact(record) + "\n")
        logger.info("audit %s", record)

    async def submit_practice(
        self,
        archive_code: str,
        request_id: str,
        qid: str,
        score: int,
    ) -> dict:
        validate_archive(archive_code)
        validate_request_id(request_id)
        validate_qid(qid)
        validate_score(score)

        with self._write_lock:
            return await self._submit_locked(
                archive_code, request_id, qid, score
            )

    async def _submit_locked(
        self, archive_code: str, request_id: str, qid: str, score: int
    ) -> dict:
        async with open_builtin_runtime(self.config) as runtime:
            memory, scope_id = await self._get_memory_and_scope(
                runtime, archive_code
            )
            records = await self._read_records(memory)

            existing = next(
                ((r, c) for r, c in records if r["request_id"] == request_id),
                None,
            )
            if existing is not None:
                prev_data, prev_citation = existing
                if (
                    prev_data["qid"] == qid
                    and prev_data["score"] == score
                ):
                    self._audit(
                        "replay",
                        scope_id=scope_id,
                        request_id=request_id,
                        citation=prev_citation,
                    )
                    return {
                        "replayed": True,
                        "request_id": request_id,
                        "qid": qid,
                        "score": score,
                        "sequence": prev_data["sequence"],
                        "citation": prev_citation,
                    }
                raise ConflictError(
                    "request_id 已用于不同的练习数据，请更换 request_id"
                )

            next_seq = max((r["sequence"] for r, _ in records), default=0) + 1
            entry = {
                "archive_code": archive_code,
                "request_id": request_id,
                "qid": qid,
                "score": score,
                "sequence": next_seq,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            serialized = compact(entry)
            if len(serialized.encode("utf-8")) > 7800:
                raise ValueError("练习记录序列化过长")

            result = await memory.remember(
                RememberMemoryRequest(
                    entries=(MemoryEntryInput(kind=ENTRY_KIND, text=serialized),)
                )
            )
            citation = result.entry.citation.model_dump(mode="json")
            sha = hashlib.sha256(serialized.encode()).hexdigest()
            self._audit(
                "write",
                scope_id=scope_id,
                request_id=request_id,
                qid=qid,
                score=score,
                sequence=next_seq,
                citation=citation,
                content_sha256=sha,
            )
            return {
                "replayed": False,
                "request_id": request_id,
                "qid": qid,
                "score": score,
                "sequence": next_seq,
                "citation": citation,
            }

    async def get_progress(self, archive_code: str) -> dict:
        validate_archive(archive_code)
        async with open_builtin_runtime(self.config) as runtime:
            memory, scope_id = await self._get_memory_and_scope(
                runtime, archive_code
            )
            records_with_citations = await self._read_records(memory)
        self._audit("read", scope_id=scope_id, count=len(records_with_citations))
        return self._build_progress(records_with_citations)

    def _build_progress(
        self, records_with_citations: list[tuple[dict, dict]]
    ) -> dict:
        latest_by_qid: dict[str, tuple[dict, dict]] = {}
        for data, citation in records_with_citations:
            qid = data["qid"]
            prev = latest_by_qid.get(qid)
            if prev is None or data["sequence"] > prev[0]["sequence"]:
                latest_by_qid[qid] = (data, citation)

        scores: dict[str, int] = {}
        citations: dict[str, dict] = {}
        for qid, (data, citation) in latest_by_qid.items():
            scores[qid] = data["score"]
            citations[qid] = citation

        mastered = {qid for qid, s in scores.items() if s == 2}
        weak = {qid for qid, s in scores.items() if s == 0}
        fuzzy = {qid for qid, s in scores.items() if s == 1}
        unseen = [q.qid for q in QUESTIONS if q.qid not in scores]

        recommendation = self._recommend(scores, unseen)

        all_practices = [
            PracticeRecord(
                request_id=d["request_id"],
                qid=d["qid"],
                score=d["score"],
                sequence=d["sequence"],
                timestamp=d["timestamp"],
                citation=c,
            )
            for d, c in records_with_citations
        ]

        return {
            "scores": scores,
            "citations": citations,
            "mastered_count": len(mastered),
            "weak_qids": sorted(weak),
            "fuzzy_qids": sorted(fuzzy),
            "mastered_qids": sorted(mastered),
            "unseen_qids": unseen,
            "recommendation": recommendation,
            "recent_practices": [
                {
                    "request_id": p.request_id,
                    "qid": p.qid,
                    "score": p.score,
                    "sequence": p.sequence,
                    "timestamp": p.timestamp,
                    "citation": p.citation,
                }
                for p in all_practices[-10:]
            ],
            "total_practices": len(all_practices),
        }

    def _recommend(
        self, scores: dict[str, int], unseen: list[str]
    ) -> dict:
        weak = sorted(qid for qid, s in scores.items() if s == 0)
        fuzzy = sorted(qid for qid, s in scores.items() if s == 1)
        unseen_sorted = sorted(unseen)
        mastered = sorted(qid for qid, s in scores.items() if s == 2)

        if weak:
            qid = weak[0]
            reason = f"最近自评为「不会」，建议优先复习"
            return {
                "qid": qid,
                "reason": reason,
                "priority": "weak",
                "question": BY_ID[qid],
            }
        if fuzzy:
            qid = fuzzy[0]
            reason = f"最近自评为「模糊」，建议巩固"
            return {
                "qid": qid,
                "reason": reason,
                "priority": "fuzzy",
                "question": BY_ID[qid],
            }
        if unseen_sorted:
            qid = unseen_sorted[0]
            reason = "尚未练习过，建议开始"
            return {
                "qid": qid,
                "reason": reason,
                "priority": "unseen",
                "question": BY_ID[qid],
            }
        if mastered:
            qid = mastered[0]
            reason = "所有题目已掌握，可选择任意题目回顾"
            return {
                "qid": qid,
                "reason": reason,
                "priority": "mastered",
                "question": BY_ID[qid],
            }
        return {
            "qid": None,
            "reason": "题库为空",
            "priority": "none",
            "question": None,
        }


class ConflictError(Exception):
    pass
