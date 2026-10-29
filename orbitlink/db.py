"""Thin SQL storage layer (no ORM).

Queries are written with ``?`` placeholders. SQLite is used directly; for
PostgreSQL (``DATABASE_URL=postgresql://...``) the placeholders are rewritten
to ``%s`` and ``psycopg`` is used if it is installed. Booleans and timestamps
are stored in a form both engines accept (ISO-8601 text on SQLite).
"""
from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable, Optional

SCHEMA_SQLITE = Path(__file__).resolve().parent.parent / "db" / "schema_sqlite.sql"


class Database:
    def __init__(self, url: str = ":memory:"):
        self.url = url
        self._lock = threading.RLock()
        if url.startswith("postgres"):
            import psycopg  # type: ignore  # optional dependency

            self.dialect = "postgres"
            self.conn = psycopg.connect(url, autocommit=True)
        else:
            self.dialect = "sqlite"
            path = url.removeprefix("sqlite:///")
            self.conn = sqlite3.connect(path, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA foreign_keys = ON")
            if path != ":memory:":
                self.conn.execute("PRAGMA journal_mode = WAL")
            self.conn.executescript(SCHEMA_SQLITE.read_text())

    @classmethod
    def from_env(cls) -> "Database":
        return cls(os.environ.get("DATABASE_URL", "sqlite:///orbitlink.db"))

    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.dialect == "postgres" else sql

    def execute(self, sql: str, params: Iterable[Any] = ()) -> Optional[int]:
        """Run a statement; returns the new row id for INSERTs."""
        with self._lock:
            if self.dialect == "postgres":
                if sql.lstrip().upper().startswith("INSERT") and "RETURNING" not in sql.upper():
                    sql = sql.rstrip().rstrip(";") + " RETURNING id"
                    cur = self.conn.execute(self._sql(sql), tuple(params))
                    row = cur.fetchone()
                    return row[0] if row else None
                self.conn.execute(self._sql(sql), tuple(params))
                return None
            cur = self.conn.execute(sql, tuple(params))
            self.conn.commit()
            return cur.lastrowid

    def executemany(self, sql: str, rows: Iterable[Iterable[Any]]) -> None:
        with self._lock:
            if self.dialect == "postgres":
                with self.conn.cursor() as cur:
                    cur.executemany(self._sql(sql), [tuple(r) for r in rows])
            else:
                self.conn.executemany(sql, [tuple(r) for r in rows])
                self.conn.commit()

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            if self.dialect == "postgres":
                cur = self.conn.execute(self._sql(sql), tuple(params))
                cols = [d.name for d in cur.description]
                return [dict(zip(cols, r)) for r in cur.fetchall()]
            return [dict(r) for r in self.conn.execute(sql, tuple(params)).fetchall()]

    def one(self, sql: str, params: Iterable[Any] = ()) -> Optional[dict[str, Any]]:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def close(self) -> None:
        self.conn.close()
