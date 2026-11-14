"""Single-worker service with bounded concurrency, auth and metadata-only audit."""
from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager, closing
import hmac
import json
import logging
import os
from pathlib import Path
import sqlite3
import time
import uuid

from fastapi import FastAPI, HTTPException, Header, Depends, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, ConfigDict
from starlette.concurrency import run_in_threadpool


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    question: str = Field(min_length=1, max_length=2000)


class Audit:
    def __init__(self, path):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, at REAL, status TEXT, ms REAL)")

    def record(self, rid, status, ms):
        with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
            db.execute("INSERT INTO requests VALUES(?,?,?,?)", (rid, time.time(), status, ms))
            db.execute("DELETE FROM requests WHERE at < ?", (time.time()-7*86400,))

    def summary(self):
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute("SELECT status,COUNT(*),AVG(ms) FROM requests GROUP BY status").fetchall()
        return [{"status": s, "count": n, "mean_ms": round(ms, 2)} for s, n, ms in rows]


def create_app(engine=None, *, token=None, audit_path=None, max_inflight=2):
    supplied = engine
    @asynccontextmanager
    async def lifespan(app):
        app.state.token = token if token is not None else os.getenv("API_TOKEN", "")
        if len(app.state.token) < 24:
            raise RuntimeError("API_TOKEN must have at least 24 characters")
        app.state.audit = Audit(audit_path or os.getenv("AUDIT_PATH", "var/audit.sqlite3"))
        app.state.slots = asyncio.Semaphore(max_inflight)
        if supplied is None:
            from retriever import get_embedder
            from vector_store import get_collection
            from service.ticket_engine import TicketEngine as Engine
            collection = get_collection()
            if collection.count() == 0:
                raise RuntimeError("Index is empty; run python -m service.index first")
            app.state.engine = Engine(collection, get_embedder())
        else:
            app.state.engine = supplied
        yield
        app.state.engine.close()

    app = FastAPI(title="Aftercare Agent", version="1.0.0", lifespan=lifespan,
                  docs_url=None, redoc_url=None)

    async def authorize(authorization: str = Header(default="")):
        if not hmac.compare_digest(authorization, "Bearer " + app.state.token):
            raise HTTPException(401, "Invalid bearer token")

    @app.middleware("http")
    async def request_context(request, call_next):
        rid = uuid.uuid4().hex
        request.state.request_id = rid
        # Reject oversized streamed bodies as well as Content-Length requests.
        if request.method == "POST":
            total, parts = 0, []
            async for chunk in request.stream():
                total += len(chunk)
                if total > 16384:
                    from fastapi.responses import JSONResponse
                    return JSONResponse({"detail": "Body too large"}, status_code=413,
                                        headers={"X-Request-ID": rid})
                parts.append(chunk)
            request._body = b"".join(parts)
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/", response_class=HTMLResponse)
    def ui():
        return Path(__file__).with_name("index.html").read_text(encoding="utf-8")

    @app.get("/health/live")
    def live():
        return {"status": "live"}

    @app.get("/health/ready")
    def ready():
        return {"status": "ready", "model_configured": bool(getattr(app.state.engine, "client", None))}

    @app.get("/v1/metrics", dependencies=[Depends(authorize)])
    def metrics():
        return {"retention_days": 7, "requests": app.state.audit.summary()}

    @app.post("/v1/ask", dependencies=[Depends(authorize)])
    async def ask(body: Question, request: Request):
        try:
            await asyncio.wait_for(app.state.slots.acquire(), timeout=0.1)
        except TimeoutError:
            raise HTTPException(429, "Service busy", headers={"Retry-After": "2"})
        started, rid = time.perf_counter(), request.state.request_id
        try:
            result = await run_in_threadpool(app.state.engine.ask, body.question)
            elapsed = round((time.perf_counter()-started)*1000, 2)
            try:
                await run_in_threadpool(app.state.audit.record, rid, result["status"], elapsed)
            except sqlite3.Error:
                logging.warning("audit_write_failed request_id=%s", rid)
            logging.info(json.dumps({"event": "ask", "request_id": rid, "status": result["status"], "elapsed_ms": elapsed}))
            return {**result, "request_id": rid, "elapsed_ms": elapsed}
        finally:
            app.state.slots.release()

    return app


app = create_app()
