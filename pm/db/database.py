"""Low-level SQLite connection management.

`Database` is the only place in the codebase that touches the ``sqlite3`` module
directly. It owns the connection, sets the pragmas that give us deterministic,
consistent behaviour, and applies the schema. Everything else goes through
``Store`` (see ``store.py``).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from pm.exceptions import ConfigurationError

SCHEMA_VERSION = "2"
_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


class Database:
    """A thin, deterministic wrapper around a single SQLite connection.

    Use :meth:`connect` to open (creating the file and parent dirs as needed),
    then :meth:`apply_schema` to ensure the tables exist. All writes should go
    through the :meth:`transaction` context manager so they are atomic.
    """

    def __init__(self, conn: sqlite3.Connection, path: Path) -> None:
        self.conn = conn
        self.path = path

    # -- construction --------------------------------------------------------

    @classmethod
    def connect(cls, path: str | Path) -> "Database":
        """Open (or create) the database file at ``path``.

        Sets ``row_factory`` to :class:`sqlite3.Row`, turns on foreign-key
        enforcement, and enables WAL journaling for robust local persistence.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return cls(conn, path)

    # -- schema --------------------------------------------------------------

    def apply_schema(self) -> None:
        """Create tables if absent and stamp the schema version.

        Idempotent: the DDL uses ``CREATE TABLE IF NOT EXISTS`` and the version
        is written once. Raises if an existing DB was created by an incompatible
        schema version.
        """
        existing = self._schema_version()
        if existing is not None and existing != SCHEMA_VERSION:
            raise ConfigurationError(
                f"database at {self.path} has schema version {existing!r}, "
                f"but this build expects {SCHEMA_VERSION!r}",
                details={"path": str(self.path), "found": existing, "expected": SCHEMA_VERSION},
            )
        with self.transaction():
            self.conn.executescript(_SCHEMA_PATH.read_text())
            if existing is None:
                self.conn.execute(
                    "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
                    (SCHEMA_VERSION,),
                )

    def _schema_version(self) -> str | None:
        # `meta` may not exist yet on a brand-new file.
        row = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='meta'"
        ).fetchone()
        if row is None:
            return None
        row = self.conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
        return row["value"] if row else None

    # -- execution helpers ---------------------------------------------------

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Atomic unit of work: commits on success, rolls back on exception."""
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        """Run a single statement (autocommit-style, wrapped in a transaction)."""
        with self.transaction():
            return self.conn.execute(sql, params)

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, params).fetchone()

    def query_all(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchall()

    def backup_to(self, path: str | Path) -> None:
        """Write a self-contained copy of this database to ``path``.

        Uses SQLite's online backup API so the snapshot is consistent even with
        WAL journaling active. The destination is a complete standalone file
        (no WAL sidecar), safe to copy or open read-only afterwards.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        dest = sqlite3.connect(str(path))
        try:
            with dest:
                self.conn.backup(dest)
        finally:
            dest.close()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
