import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

_default_data = Path(__file__).resolve().parent.parent / "data"
DATA_DIR = Path(os.environ.get("HOUSEKEEPER_DATA_DIR") or _default_data)
DB_PATH = DATA_DIR / "housekeeper.db"

_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


_conn = _connect()


def init() -> None:
    with _lock, _conn:
        _conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS config (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS items (
                source     TEXT NOT NULL,           -- 'radarr' | 'sonarr'
                source_id  INTEGER NOT NULL,        -- Radarr/Sonarr internal id
                tmdb_id    INTEGER,
                title      TEXT NOT NULL,
                year       INTEGER,
                kind       TEXT NOT NULL,           -- 'movie' | 'tv'
                poster_url TEXT,
                providers  TEXT NOT NULL DEFAULT '[]',  -- JSON array of provider ids found
                provider_names TEXT NOT NULL DEFAULT '[]',
                size_bytes INTEGER DEFAULT 0,
                arr_path   TEXT,                     -- path fragment for deep-link
                watched    INTEGER NOT NULL DEFAULT 0, -- 0 unknown/unwatched, 1 fully watched, 2 in progress
                view_count INTEGER NOT NULL DEFAULT 0,
                total_episodes INTEGER,
                last_viewed_at TEXT,
                plex_rating_key TEXT,
                last_scan  TEXT,
                PRIMARY KEY (source, source_id)
            );

            CREATE TABLE IF NOT EXISTS ignored (
                source     TEXT NOT NULL,
                source_id  INTEGER NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (source, source_id)
            );

            CREATE TABLE IF NOT EXISTS scan_status (
                id         INTEGER PRIMARY KEY CHECK (id = 1),
                running    INTEGER NOT NULL DEFAULT 0,
                phase      TEXT,
                processed  INTEGER NOT NULL DEFAULT 0,
                total      INTEGER NOT NULL DEFAULT 0,
                started_at TEXT,
                finished_at TEXT,
                error      TEXT
            );
            INSERT OR IGNORE INTO scan_status (id, running) VALUES (1, 0);
            """
        )
        # Lightweight column migration for existing DBs
        existing = {row["name"] for row in _conn.execute("PRAGMA table_info(items)").fetchall()}
        for col, ddl in (
            ("watched", "INTEGER NOT NULL DEFAULT 0"),
            ("view_count", "INTEGER NOT NULL DEFAULT 0"),
            ("total_episodes", "INTEGER"),
            ("last_viewed_at", "TEXT"),
            ("arr_path", "TEXT"),
            ("plex_rating_key", "TEXT"),
        ):
            if col not in existing:
                _conn.execute(f"ALTER TABLE items ADD COLUMN {col} {ddl}")


# ---- config helpers ----------------------------------------------------------

DEFAULT_CONFIG: dict[str, Any] = {
    "radarr_url": "",
    "radarr_api_key": "",
    "sonarr_url": "",
    "sonarr_api_key": "",
    "tmdb_api_key": "",
    "plex_url": "",
    "plex_token": "",
    "region": "US",
    # provider IDs to watch for (TMDB / JustWatch ids). 8 = Netflix, 337 = Disney+
    "providers": [8, 337],
}


def get_config() -> dict[str, Any]:
    with _lock:
        rows = _conn.execute("SELECT key, value FROM config").fetchall()
    cfg = dict(DEFAULT_CONFIG)
    for row in rows:
        try:
            cfg[row["key"]] = json.loads(row["value"])
        except json.JSONDecodeError:
            cfg[row["key"]] = row["value"]
    return cfg


def set_config(updates: dict[str, Any]) -> dict[str, Any]:
    with _lock, _conn:
        for key, value in updates.items():
            _conn.execute(
                "INSERT INTO config(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)),
            )
    return get_config()


# ---- items -------------------------------------------------------------------

def upsert_item(row: dict[str, Any]) -> None:
    with _lock, _conn:
        _conn.execute(
            """
            INSERT INTO items (source, source_id, tmdb_id, title, year, kind,
                               poster_url, providers, provider_names, size_bytes,
                               arr_path, watched, view_count, total_episodes,
                               last_viewed_at, plex_rating_key, last_scan)
            VALUES (:source, :source_id, :tmdb_id, :title, :year, :kind,
                    :poster_url, :providers, :provider_names, :size_bytes,
                    :arr_path, :watched, :view_count, :total_episodes,
                    :last_viewed_at, :plex_rating_key, datetime('now'))
            ON CONFLICT(source, source_id) DO UPDATE SET
                tmdb_id=excluded.tmdb_id,
                title=excluded.title,
                year=excluded.year,
                kind=excluded.kind,
                poster_url=excluded.poster_url,
                providers=excluded.providers,
                provider_names=excluded.provider_names,
                size_bytes=excluded.size_bytes,
                arr_path=excluded.arr_path,
                watched=excluded.watched,
                view_count=excluded.view_count,
                total_episodes=excluded.total_episodes,
                last_viewed_at=excluded.last_viewed_at,
                plex_rating_key=excluded.plex_rating_key,
                last_scan=excluded.last_scan
            """,
            row,
        )


def clear_items(source: str) -> None:
    with _lock, _conn:
        _conn.execute("DELETE FROM items WHERE source = ?", (source,))


def list_items(include_ignored: bool = False,
               provider_filter: list[int] | None = None,
               mode: str = "streaming") -> list[dict[str, Any]]:
    """mode: 'streaming' (has at least one matched provider),
              'watched'   (fully watched on Plex),
              'both'      (streaming AND watched — cleanup candidates),
              'all'       (no constraint)."""
    sql = (
        "SELECT i.*, "
        "  CASE WHEN ig.source IS NOT NULL THEN 1 ELSE 0 END AS ignored "
        "FROM items i "
        "LEFT JOIN ignored ig ON ig.source = i.source AND ig.source_id = i.source_id "
        "WHERE 1=1"
    )
    if mode == "streaming":
        sql += " AND json_array_length(i.providers) > 0"
    elif mode == "watched":
        sql += " AND i.watched = 1"
    elif mode == "both":
        sql += " AND json_array_length(i.providers) > 0 AND i.watched = 1"
    # 'all' adds no constraint
    if not include_ignored:
        sql += " AND ig.source IS NULL"
    sql += " ORDER BY i.title COLLATE NOCASE"
    with _lock:
        rows = [dict(r) for r in _conn.execute(sql).fetchall()]
    out: list[dict[str, Any]] = []
    for r in rows:
        try:
            r["providers"] = json.loads(r["providers"])
        except json.JSONDecodeError:
            r["providers"] = []
        try:
            r["provider_names"] = json.loads(r["provider_names"])
        except json.JSONDecodeError:
            r["provider_names"] = []
        if provider_filter:
            if not set(provider_filter) & set(r["providers"]):
                continue
        out.append(r)
    return out


def get_item(source: str, source_id: int) -> dict[str, Any] | None:
    with _lock:
        row = _conn.execute(
            "SELECT * FROM items WHERE source=? AND source_id=?",
            (source, source_id),
        ).fetchone()
    return dict(row) if row else None


def delete_item(source: str, source_id: int) -> None:
    with _lock, _conn:
        _conn.execute(
            "DELETE FROM items WHERE source=? AND source_id=?",
            (source, source_id),
        )


def set_plex_rating_key(source: str, source_id: int, rating_key: str) -> None:
    with _lock, _conn:
        _conn.execute(
            "UPDATE items SET plex_rating_key=? WHERE source=? AND source_id=?",
            (rating_key, source, source_id),
        )


# ---- ignore list -------------------------------------------------------------

def ignore(source: str, source_id: int) -> None:
    with _lock, _conn:
        _conn.execute(
            "INSERT OR IGNORE INTO ignored (source, source_id) VALUES (?, ?)",
            (source, source_id),
        )


def unignore(source: str, source_id: int) -> None:
    with _lock, _conn:
        _conn.execute(
            "DELETE FROM ignored WHERE source=? AND source_id=?",
            (source, source_id),
        )


def is_ignored(source: str, source_id: int) -> bool:
    with _lock:
        r = _conn.execute(
            "SELECT 1 FROM ignored WHERE source=? AND source_id=?",
            (source, source_id),
        ).fetchone()
    return r is not None


# ---- scan status -------------------------------------------------------------

def set_scan_status(**fields: Any) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    vals = list(fields.values())
    with _lock, _conn:
        _conn.execute(f"UPDATE scan_status SET {cols} WHERE id=1", vals)


def get_scan_status() -> dict[str, Any]:
    with _lock:
        row = _conn.execute("SELECT * FROM scan_status WHERE id=1").fetchone()
    return dict(row) if row else {}
