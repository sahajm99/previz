"""One SSE envelope for every stream in the product (design spec §11.2).

One envelope means the client has one parser and the Trace tab renders every
surface without special cases. It also means the UI never needs a spinner: a
spinner says "something is happening", and these events say what.

Usage, from any endpoint:

    @router.post("/api/thing")
    def thing():
        def work(emit):
            emit.thinking("Cinematographer", "reading the scene")
            ...
            return {"ok": True}
        return stream(work, agent="Cinematographer")

The worker runs in a thread, so blocking Vertex calls are fine inside it.
"""
from __future__ import annotations

import json
import queue
import threading
import time
import traceback
import uuid
from typing import Any, Callable, Iterator

from fastapi.responses import StreamingResponse

HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    # Cloud Run and any nginx in between will buffer a stream to death otherwise,
    # and a buffered SSE stream looks exactly like a hung request.
    "X-Accel-Buffering": "no",
}


class Emit:
    """The event writer handed to a worker. Every method is fire and forget."""

    def __init__(self, q: "queue.Queue[str | None]", run_id: str, agent: str):
        self._q = q
        self.run_id = run_id
        self.agent = agent
        self.t0 = time.time()

    def _put(self, t: str, **kw: Any) -> None:
        payload = {"t": t, "run_id": self.run_id, **kw}
        self._q.put("data: " + json.dumps(payload, default=str) + "\n\n")

    # The envelope, exactly as §11.2 defines it.
    def run_start(self, agent: str | None = None) -> None:
        self._put("run_start", agent=agent or self.agent)

    def thinking(self, text: str, agent: str | None = None) -> None:
        self._put("thinking", agent=agent or self.agent, text=text)

    def tool_call(self, tool: str, args: Any = None,
                  agent: str | None = None) -> None:
        self._put("tool_call", agent=agent or self.agent, tool=tool, args=args)

    def tool_result(self, tool: str, summary: str, ms: int = 0) -> None:
        self._put("tool_result", tool=tool, summary=summary, ms=ms)

    def context(self, slots: dict, chunk_ids: list[str],
                dropped: list[str] | None = None) -> None:
        self._put("context", slots=slots, chunk_ids=chunk_ids,
                  dropped=dropped or [])

    def partial(self, field: str, text: str) -> None:
        self._put("partial", field=field, text=text)

    def shot_ready(self, shot: dict) -> None:
        self._put("shot_ready", shot_id=shot.get("id"), shot=shot,
                  url=shot.get("image_url"),
                  face_scores=shot.get("face_scores", {}))

    def line_ready(self, line: dict) -> None:
        """Dialogue equivalent of shot_ready: a line plus its voice score."""
        self._put("line_ready", line=line)

    def proposal(self, proposal_id: str, field: str, rationale: str) -> None:
        self._put("proposal", proposal_id=proposal_id, field=field,
                  rationale=rationale)

    def violation(self, kind: str, detail: str, iteration: int = 1) -> None:
        self._put("violation", kind=kind, detail=detail, iteration=iteration)

    def data(self, **kw: Any) -> None:
        """Surface specific payload that is not a generation event."""
        self._put("data", **kw)

    def run_end(self, **kw: Any) -> None:
        self._put("run_end", ms=int((time.time() - self.t0) * 1000), **kw)

    def error(self, message: str, retryable: bool = False) -> None:
        self._put("error", message=message, retryable=retryable)


def stream(work: Callable[[Emit], Any], agent: str = "agent") -> StreamingResponse:
    """Run `work` in a thread and stream its events.

    A worker that raises produces an `error` event and a clean `run_end` rather
    than a dropped connection. On a demo stage a visible error beats a UI that
    silently stops moving.
    """
    q: "queue.Queue[str | None]" = queue.Queue()
    run_id = uuid.uuid4().hex[:12]
    emit = Emit(q, run_id, agent)

    def runner() -> None:
        emit.run_start()
        try:
            result = work(emit)
            if isinstance(result, dict):
                emit.data(**result)
            emit.run_end(ok=True)
        except Exception as exc:                        # noqa: BLE001
            traceback.print_exc()
            emit.error(f"{type(exc).__name__}: {exc}",
                       retryable="RESOURCE_EXHAUSTED" in str(exc)
                       or "429" in str(exc))
            emit.run_end(ok=False)
        finally:
            q.put(None)

    threading.Thread(target=runner, daemon=True).start()

    def gen() -> Iterator[str]:
        # Comment frame first: it opens the stream immediately so the browser
        # fires onopen, and it defeats any proxy waiting for first output.
        yield ": open\n\n"
        while True:
            try:
                item = q.get(timeout=15)
            except queue.Empty:
                yield ": keepalive\n\n"
                continue
            if item is None:
                return
            yield item

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers=HEADERS)
