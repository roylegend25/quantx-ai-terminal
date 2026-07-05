"""Read-only view onto the backend's structured event stream.

Backed entirely by the in-memory ring buffer in app.monitoring.logging -
there is no separate log store, no file access and no shell/DB introspection
here, so this surface can't leak anything beyond what the backend already
chose to log via log_event() (never secrets, tokens or .env values).
"""
import json

from fastapi import APIRouter, Query
from starlette.responses import StreamingResponse

from app.monitoring.logging import get_recent_logs, subscribe_logs, unsubscribe_logs

router = APIRouter(prefix="/api/logs", tags=["logs"])

VALID_LEVELS = {"INFO", "WARNING", "ERROR"}
VALID_CATEGORIES = {"prediction", "trading", "risk", "api", "scheduler", "system"}


def _clean(level: str | None, category: str | None) -> tuple[str | None, str | None]:
    if level and level.upper() not in VALID_LEVELS:
        level = None
    if category and category.lower() not in VALID_CATEGORIES:
        category = None
    return level, category


@router.get("/recent")
async def recent_logs(
    limit: int = Query(200, ge=1, le=1000),
    since_id: int | None = None,
    level: str | None = None,
    category: str | None = None,
):
    level, category = _clean(level, category)
    events = get_recent_logs(limit=limit, since_id=since_id, level=level, category=category)
    return {"events": events, "count": len(events)}


@router.get("/stream")
async def stream_logs(level: str | None = None, category: str | None = None):
    level, category = _clean(level, category)

    async def event_source():
        queue = subscribe_logs()
        try:
            for event in get_recent_logs(limit=20, level=level, category=category):
                yield f"data: {json.dumps(event)}\n\n"
            while True:
                event = await queue.get()
                if level and event.get("level") != level:
                    continue
                if category and event.get("category") != category:
                    continue
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            unsubscribe_logs(queue)

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
