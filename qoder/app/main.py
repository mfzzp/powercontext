"""aiohttp 后端：API 路由 + 静态文件服务。"""
from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from pathlib import Path

from aiohttp import web

from .memory_service import ConflictError, MemoryService, validate_archive
from .questions import BY_ID, BY_TOPIC, TOPICS, question_summary, topic_overview

ROOT = Path(__file__).resolve().parent
APP_DIR = ROOT
STATIC_DIR = APP_DIR / "static"
DEFAULT_DATA_DIR = ROOT.parent / "data"

logger = logging.getLogger("review-station")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


def _json_response(data: dict, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


def _error(message: str, status: int = 400) -> web.Response:
    return _json_response({"error": message}, status=status)


def _read_body(request_json: dict) -> dict:
    if not isinstance(request_json, dict):
        raise ValueError("请求体必须是 JSON 对象")
    return request_json


class ReviewApp:
    def __init__(self, data_dir: Path):
        self.service = MemoryService(data_dir)

    async def health(self, request: web.Request) -> web.Response:
        return _json_response({"status": "ok", "service": "review-station"})

    async def list_topics(self, request: web.Request) -> web.Response:
        return _json_response({
            "topics": TOPICS,
            "overviews": {t: topic_overview(t) for t in TOPICS},
        })

    async def get_topic(self, request: web.Request) -> web.Response:
        topic = request.match_info["topic"]
        if topic not in TOPICS:
            return _error(f"无效主题：{topic}", 404)
        return _json_response(topic_overview(topic))

    async def get_question(self, request: web.Request) -> web.Response:
        qid = request.match_info["qid"]
        if qid not in BY_ID:
            return _error(f"无效题目 ID：{qid}", 404)
        q = BY_ID[qid]
        return _json_response({
            "topic": q.topic,
            "qid": q.qid,
            "question": q.question,
            "answer": q.answer,
            "knowledge": q.knowledge,
        })

    async def get_progress(self, request: web.Request) -> web.Response:
        archive = request.query.get("archive", "")
        try:
            validate_archive(archive)
        except ValueError as e:
            return _error(str(e))
        try:
            progress = await self.service.get_progress(archive)
        except Exception:
            logger.exception("读取进度失败")
            return _error("记忆服务暂不可读，请稍后重试", 503)
        return _json_response(self._serialize_progress(progress))

    async def submit_score(self, request: web.Request) -> web.Response:
        try:
            body = _read_body(await request.json())
        except (json.JSONDecodeError, Exception) as e:
            return _error(f"无效 JSON：{e}")

        archive = body.get("archive", "")
        request_id = body.get("request_id", "")
        qid = body.get("qid", "")
        raw_score = body.get("score")

        try:
            validate_archive(archive)
        except ValueError as e:
            return _error(str(e))

        if not request_id:
            request_id = uuid.uuid4().hex

        try:
            result = await self.service.submit_practice(
                archive, request_id, qid, raw_score
            )
        except ValueError as e:
            return _error(str(e))
        except ConflictError as e:
            return _error(str(e), 409)
        except Exception:
            logger.exception("保存练习失败")
            return _error("保存失败，记忆服务暂不可用", 500)

        q = BY_ID.get(qid)
        resp = {
            "replayed": result["replayed"],
            "request_id": result["request_id"],
            "qid": result["qid"],
            "score": result["score"],
            "sequence": result["sequence"],
            "citation": result["citation"],
            "knowledge": q.knowledge if q else None,
        }
        return _json_response(resp)

    async def recommend(self, request: web.Request) -> web.Response:
        archive = request.query.get("archive", "")
        try:
            validate_archive(archive)
        except ValueError as e:
            return _error(str(e))
        try:
            progress = await self.service.get_progress(archive)
        except Exception:
            logger.exception("获取推荐失败")
            return _error("记忆服务暂不可读，请稍后重试", 503)

        rec = progress["recommendation"]
        q = rec.get("question")
        return _json_response({
            "qid": rec["qid"],
            "reason": rec["reason"],
            "priority": rec["priority"],
            "question": {
                "qid": q.qid,
                "topic": q.topic,
                "question": q.question,
                "knowledge": q.knowledge,
            } if q else None,
        })

    def _serialize_progress(self, progress: dict) -> dict:
        rec = progress["recommendation"]
        q = rec.get("question")
        return {
            "scores": progress["scores"],
            "citations": progress["citations"],
            "mastered_count": progress["mastered_count"],
            "weak_qids": progress["weak_qids"],
            "fuzzy_qids": progress["fuzzy_qids"],
            "mastered_qids": progress["mastered_qids"],
            "unseen_qids": progress["unseen_qids"],
            "recent_practices": progress["recent_practices"],
            "total_practices": progress["total_practices"],
            "recommendation": {
                "qid": rec["qid"],
                "reason": rec["reason"],
                "priority": rec["priority"],
                "question": {
                    "qid": q.qid,
                    "topic": q.topic,
                    "question": q.question,
                    "knowledge": q.knowledge,
                } if q else None,
            },
        }


def create_app(data_dir: Path | None = None) -> web.Application:
    if data_dir is None:
        data_dir = Path(os.environ.get("REVIEW_DATA_DIR", str(DEFAULT_DATA_DIR)))
    review = ReviewApp(data_dir)
    app = web.Application()
    app.router.add_get("/api/health", review.health)
    app.router.add_get("/api/topics", review.list_topics)
    app.router.add_get("/api/topics/{topic}", review.get_topic)
    app.router.add_get("/api/questions/{qid}", review.get_question)
    app.router.add_get("/api/progress", review.get_progress)
    app.router.add_post("/api/submit", review.submit_score)
    app.router.add_get("/api/recommend", review.recommend)
    if STATIC_DIR.is_dir():
        async def index_handler(request):
            return web.FileResponse(STATIC_DIR / "index.html")
        app.router.add_get("/", index_handler)
        app.router.add_static("/static", STATIC_DIR, name="static")
    return app


def main():
    port = int(os.environ.get("REVIEW_PORT", "8080"))
    host = os.environ.get("REVIEW_HOST", "127.0.0.1")
    data_dir = Path(os.environ.get("REVIEW_DATA_DIR", str(DEFAULT_DATA_DIR)))
    logger.info("启动复习接力站 %s:%s data=%s", host, port, data_dir)
    web.run_app(create_app(data_dir), host=host, port=port, print=None)


if __name__ == "__main__":
    main()
