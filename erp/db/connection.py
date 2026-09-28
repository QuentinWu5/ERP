"""Couche d'accès data : connexion SQLite centralisée.

Tout le SQL de l'application passe par erp/db/ — les services ne
manipulent jamais sqlite3 directement. Le passage vers PostgreSQL/MySQL
se fera en adaptant uniquement cette couche.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable, Optional

DEFAULT_DB_PATH = Path("erp.db")

# Chemin de la base, résolu au démarrage par main.py / les tests.
_db_path: Path = DEFAULT_DB_PATH


def set_db_path(path: str | Path) -> None:
    global _db_path
    _db_path = Path(path)
    _db_path.parent.mkdir(parents=True, exist_ok=True)


def get_db_path() -> Path:
    return _db_path


def connect() -> sqlite3.Connection:
    """Ouvre une connexion avec WAL, foreign keys et row factory dict."""
    conn = sqlite3.connect(str(_db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


class Database:
    """Façade d'accès aux données utilisée par tous les services."""

    def __init__(self, conn: Optional[sqlite3.Connection] = None):
        self.conn = conn if conn is not None else connect()
        self._in_transaction = False
        self._depth = 0

    # -- API générique -------------------------------------------------
    def query(
        self, sql: str, params: Iterable[Any] = ()
    ) -> list[dict[str, Any]]:
        cur = self.conn.execute(sql, tuple(params))
        return [dict(row) for row in cur.fetchall()]

    def query_one(
        self, sql: str, params: Iterable[Any] = ()
    ) -> Optional[dict[str, Any]]:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        """Exécute une écriture ; renvoie lastrowid."""
        cur = self.conn.execute(sql, tuple(params))
        if not self._in_transaction:
            self.conn.commit()
        return cur.lastrowid

    def execute_many(self, sql: str, seq: Iterable[Iterable[Any]]) -> None:
        self.conn.executemany(sql, [tuple(p) for p in seq])
        if not self._in_transaction:
            self.conn.commit()

    # -- Transactions (imbriquables via SAVEPOINT) ----------------------
    def begin(self) -> None:
        if self._depth == 0:
            self.conn.execute("BEGIN IMMEDIATE")
        else:
            self.conn.execute(f"SAVEPOINT sp_{self._depth}")
        self._depth += 1

    def commit(self) -> None:
        if self._depth == 0:
            self.conn.commit()
            return
        self._depth -= 1
        if self._depth == 0:
            self.conn.commit()
        else:
            self.conn.execute(f"RELEASE SAVEPOINT sp_{self._depth}")

    def rollback(self) -> None:
        if self._depth == 0:
            self.conn.rollback()
            return
        self._depth -= 1
        if self._depth == 0:
            self.conn.rollback()
        else:
            self.conn.execute(f"ROLLBACK TO SAVEPOINT sp_{self._depth}")

    def transaction(self):
        return _Transaction(self)

    def close(self) -> None:
        self.conn.close()

    def backup(self, dest: str | Path) -> Path:
        """Sauvegarde simple : copie datée de la base."""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        src = sqlite3.connect(str(_db_path))
        dst = sqlite3.connect(str(dest))
        with dst:
            src.backup(dst)
        src.close()
        dst.close()
        return dest


class _Transaction:
    """Context manager : commit si OK, rollback si exception.

    Usage :
        with db.transaction():
            db.execute(...)  # execute ne commit pas dans une transaction
    """

    def __init__(self, db: Database):
        self.db = db

    def __enter__(self) -> Database:
        self.db.begin()
        self.db._in_transaction = True
        return self.db

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.db._in_transaction = self.db._depth > 1
        if exc_type is None:
            self.db.commit()
        else:
            self.db.rollback()
        return False
