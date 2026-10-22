"""Append-only event store.

Every human action is an immutable event: (id, at, actor, type, target, data, batch).
Nothing is updated or deleted - undo is itself an event that voids earlier ones - so the
workpaper keeps full history, who/when/why, and can be replayed to any point in time.

SQLite locally; set DATABASE_URL (e.g. a Neon/Supabase Postgres URL) in production so
saved work survives restarts of free hosting.
"""
from __future__ import annotations

import base64
import json
import os
import uuid
from datetime import datetime, timezone

from sqlalchemy import (Column, Integer, LargeBinary, MetaData, String, Table, Text, create_engine, insert,
                        select)

meta = MetaData()
events = Table(
    "events", meta,
    Column("seq", Integer, primary_key=True, autoincrement=True),
    Column("id", String(40), unique=True, nullable=False),
    Column("at", String(40), nullable=False),
    Column("actor", String(20), nullable=False),
    Column("type", String(60), nullable=False),
    Column("target", String(200)),
    Column("batch", String(40)),
    Column("data", Text, nullable=False),
)
files = Table(
    "files", meta,
    Column("id", String(40), primary_key=True),
    Column("name", String(300), nullable=False),
    Column("mime", String(100)),
    Column("size", Integer),
    Column("data", LargeBinary),
    Column("at", String(40)),
    Column("actor", String(20)),
)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Store:
    def __init__(self, url: str | None = None):
        url = url or os.environ.get("DATABASE_URL") or "sqlite:///workpaper.db"
        if url.startswith("postgres://"):
            url = "postgresql+psycopg://" + url[len("postgres://"):]
        elif url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://"):]
        self.url = url
        self.engine = create_engine(url, future=True, pool_pre_ping=True)
        meta.create_all(self.engine)

    def append(self, actor: str, type_: str, target: str | None = None, data: dict | None = None,
               batch: str | None = None, at: str | None = None) -> dict:
        ev = {"id": "e-" + uuid.uuid4().hex[:12], "at": at or now_iso(), "actor": actor, "type": type_,
              "target": target, "batch": batch, "data": data or {}}
        with self.engine.begin() as cx:
            cx.execute(insert(events).values(**{**ev, "data": json.dumps(ev["data"])}))
        return ev

    def all(self) -> list[dict]:
        with self.engine.connect() as cx:
            rows = cx.execute(select(events).order_by(events.c.seq)).mappings().all()
        return [{**dict(r), "data": json.loads(r["data"])} for r in rows]

    def put_file(self, name: str, mime: str, b64: str, actor: str) -> dict:
        raw = base64.b64decode(b64)
        if len(raw) > 5_000_000:
            raise ValueError("File too large (5 MB max in this prototype)")
        fid = "f-" + uuid.uuid4().hex[:10]
        with self.engine.begin() as cx:
            cx.execute(insert(files).values(id=fid, name=name, mime=mime, size=len(raw), data=raw, at=now_iso(), actor=actor))
        return {"id": fid, "name": name, "mime": mime, "size": len(raw)}

    def get_file(self, fid: str):
        with self.engine.connect() as cx:
            r = cx.execute(select(files).where(files.c.id == fid)).mappings().first()
        return dict(r) if r else None

    def reset(self):
        with self.engine.begin() as cx:
            cx.execute(events.delete())
            cx.execute(files.delete())
