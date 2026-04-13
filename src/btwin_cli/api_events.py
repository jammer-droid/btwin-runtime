"""SSE endpoint for real-time push notifications."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from btwin_core.event_bus import EventBus


def create_events_router(event_bus: EventBus) -> APIRouter:
    router = APIRouter()

    @router.get("/api/events")
    async def sse_stream(request: Request):
        queue = event_bus.subscribe()

        async def generate():
            try:
                yield ": connected\n\n"
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=30.0)
                        yield event.to_sse()
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
                    if await request.is_disconnected():
                        break
            finally:
                event_bus.unsubscribe(queue)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router
