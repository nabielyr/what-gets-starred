"""SQLite storage for the collector.

Every stage writes its results row by row and commits immediately, so the
pipeline can be stopped at any point and resumed without refetching data.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- One stratum = star bucket x creation year x language group.
CREATE TABLE IF NOT EXISTS strata (
    key         TEXT PRIMARY KEY,           -- "bucket|year|lang_group"
    bucket      TEXT NOT NULL,
    year        INTEGER NOT NULL,
    lang_group  TEXT NOT NULL,
    quota       INTEGER NOT NULL DEFAULT 0,
    total_count INTEGER,                    -- population size from the search API (census)
    attempts    INTEGER NOT NULL DEFAULT 0, -- search windows tried so far
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending | done | exhausted
    updated_at  TEXT
);

-- Raw search result pages, so a resumed run never repeats a search request.
CREATE TABLE IF NOT EXISTS search_cache (
    query       TEXT NOT NULL,
    page        INTEGER NOT NULL,
    total_count INTEGER NOT NULL,
    incomplete  INTEGER NOT NULL,
    items_json  TEXT NOT NULL,
    fetched_at  TEXT NOT NULL,
    PRIMARY KEY (query, page)
);

CREATE TABLE IF NOT EXISTS repos (
    id            INTEGER PRIMARY KEY,
    full_name     TEXT NOT NULL UNIQUE,
    stratum       TEXT NOT NULL,
    bucket        TEXT NOT NULL,
    lang_group    TEXT NOT NULL,
    stars         INTEGER,
    forks         INTEGER,
    open_issues   INTEGER,
    language      TEXT,
    created_at    TEXT,
    pushed_at     TEXT,
    updated_at    TEXT,
    license       TEXT,
    topics        TEXT,                     -- JSON list
    size_kb       INTEGER,
    archived      INTEGER,
    homepage      TEXT,
    description   TEXT,
    raw_json      TEXT,
    discovered_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS repo_details (
    repo_id        INTEGER PRIMARY KEY REFERENCES repos(id),
    status         TEXT NOT NULL,           -- ok | not_found | blocked | error
    http_status    INTEGER,
    stars          INTEGER,
    forks          INTEGER,
    subscribers    INTEGER,                 -- real watchers (watchers_count == stars in the API)
    open_issues    INTEGER,
    network_count  INTEGER,
    license        TEXT,
    topics         TEXT,
    default_branch TEXT,
    archived       INTEGER,
    pushed_at      TEXT,
    raw_json       TEXT,
    fetched_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS readmes (
    repo_id     INTEGER PRIMARY KEY REFERENCES repos(id),
    status      TEXT NOT NULL,              -- ok | missing | blocked | error
    http_status INTEGER,
    path        TEXT,
    size        INTEGER,
    content     TEXT,
    fetched_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS commit_activity (
    repo_id     INTEGER PRIMARY KEY REFERENCES repos(id),
    status      TEXT NOT NULL,              -- ok | pending | empty | unavailable | not_found | error
    http_status INTEGER,
    attempts    INTEGER NOT NULL DEFAULT 0,
    weeks_json  TEXT,                       -- 52 x {"week": epoch, "total": n}
    total_52w   INTEGER,
    fetched_at  TEXT NOT NULL
);
"""

# Statuses after which a stage is never retried for that repo.
FINAL_STATUSES = {
    "repo_details": ("ok", "not_found", "blocked"),
    "readmes": ("ok", "missing", "blocked"),
    "commit_activity": ("ok", "empty", "unavailable", "not_found", "blocked"),
}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path | str):
        path = Path(path)
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------------ meta
    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.conn.commit()

    # ------------------------------------------------------------------ strata
    def upsert_stratum(self, key: str, bucket: str, year: int, lang_group: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO strata (key, bucket, year, lang_group, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (key, bucket, year, lang_group, utcnow()),
        )

    def update_stratum(self, key: str, **fields) -> None:
        if not fields:
            return
        fields["updated_at"] = utcnow()
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(f"UPDATE strata SET {cols} WHERE key = ?", (*fields.values(), key))
        self.conn.commit()

    def strata(self, where: str = "1=1", params: Iterable = ()) -> list[sqlite3.Row]:
        return self.conn.execute(
            f"SELECT * FROM strata WHERE {where} ORDER BY bucket, year, lang_group", tuple(params)
        ).fetchall()

    def sampled_count(self, stratum_key: str) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM repos WHERE stratum = ?", (stratum_key,)
        ).fetchone()[0]

    def bucket_count(self, bucket: str) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM repos WHERE bucket = ?", (bucket,)
        ).fetchone()[0]

    # ------------------------------------------------------------------ search cache
    def get_search_page(self, query: str, page: int) -> dict | None:
        row = self.conn.execute(
            "SELECT total_count, incomplete, items_json FROM search_cache WHERE query = ? AND page = ?",
            (query, page),
        ).fetchone()
        if row is None:
            return None
        return {
            "total_count": row["total_count"],
            "incomplete_results": bool(row["incomplete"]),
            "items": json.loads(row["items_json"]),
        }

    def save_search_page(self, query: str, page: int, payload: dict) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO search_cache VALUES (?, ?, ?, ?, ?, ?)",
            (
                query,
                page,
                payload.get("total_count", 0),
                int(payload.get("incomplete_results", False)),
                json.dumps(payload.get("items", [])),
                utcnow(),
            ),
        )
        self.conn.commit()

    # ------------------------------------------------------------------ repos
    def has_repo(self, repo_id: int) -> bool:
        return self.conn.execute("SELECT 1 FROM repos WHERE id = ?", (repo_id,)).fetchone() is not None

    def insert_repo(self, item: dict, stratum_key: str, bucket: str, lang_group: str) -> bool:
        """Insert a repo from a search result item. Returns False if it already existed."""
        license_ = (item.get("license") or {}).get("spdx_id")
        cur = self.conn.execute(
            """INSERT OR IGNORE INTO repos (
                id, full_name, stratum, bucket, lang_group, stars, forks, open_issues, language,
                created_at, pushed_at, updated_at, license, topics, size_kb, archived, homepage,
                description, raw_json, discovered_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                item["id"],
                item["full_name"],
                stratum_key,
                bucket,
                lang_group,
                item.get("stargazers_count"),
                item.get("forks_count"),
                item.get("open_issues_count"),
                item.get("language"),
                item.get("created_at"),
                item.get("pushed_at"),
                item.get("updated_at"),
                license_,
                json.dumps(item.get("topics", [])),
                item.get("size"),
                int(bool(item.get("archived"))),
                item.get("homepage"),
                item.get("description"),
                json.dumps(item),
                utcnow(),
            ),
        )
        self.conn.commit()
        return cur.rowcount == 1

    def repos_needing(self, table: str, limit: int | None = None) -> list[sqlite3.Row]:
        """Repos that do not yet have a final result in the given stage table."""
        final = FINAL_STATUSES[table]
        placeholders = ", ".join("?" for _ in final)
        sql = (
            f"SELECT r.id, r.full_name FROM repos r LEFT JOIN {table} t ON t.repo_id = r.id "
            f"WHERE t.repo_id IS NULL OR t.status NOT IN ({placeholders}) ORDER BY r.id"
        )
        if limit:
            sql += f" LIMIT {int(limit)}"
        return self.conn.execute(sql, final).fetchall()

    # ------------------------------------------------------------------ enrichment writes
    def save_details(self, repo_id: int, status: str, http_status: int, data: dict | None) -> None:
        data = data or {}
        self.conn.execute(
            "INSERT OR REPLACE INTO repo_details VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                repo_id,
                status,
                http_status,
                data.get("stargazers_count"),
                data.get("forks_count"),
                data.get("subscribers_count"),
                data.get("open_issues_count"),
                data.get("network_count"),
                (data.get("license") or {}).get("spdx_id"),
                json.dumps(data["topics"]) if "topics" in data else None,
                data.get("default_branch"),
                int(bool(data["archived"])) if "archived" in data else None,
                data.get("pushed_at"),
                json.dumps(data) if data else None,
                utcnow(),
            ),
        )
        self.conn.commit()

    def save_readme(
        self,
        repo_id: int,
        status: str,
        http_status: int,
        path: str | None = None,
        size: int | None = None,
        content: str | None = None,
    ) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO readmes VALUES (?, ?, ?, ?, ?, ?, ?)",
            (repo_id, status, http_status, path, size, content, utcnow()),
        )
        self.conn.commit()

    def commit_activity_attempts(self, repo_id: int) -> int:
        row = self.conn.execute(
            "SELECT attempts FROM commit_activity WHERE repo_id = ?", (repo_id,)
        ).fetchone()
        return row["attempts"] if row else 0

    def save_commit_activity(
        self, repo_id: int, status: str, http_status: int, weeks: list[dict] | None = None
    ) -> None:
        attempts = self.commit_activity_attempts(repo_id) + 1
        weeks_json = total = None
        if weeks is not None:
            compact = [{"week": w["week"], "total": w["total"]} for w in weeks]
            weeks_json = json.dumps(compact)
            total = sum(w["total"] for w in compact)
        self.conn.execute(
            "INSERT OR REPLACE INTO commit_activity VALUES (?, ?, ?, ?, ?, ?, ?)",
            (repo_id, status, http_status, attempts, weeks_json, total, utcnow()),
        )
        self.conn.commit()

    def pending_commit_activity(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT r.id, r.full_name, c.attempts, c.fetched_at FROM commit_activity c "
            "JOIN repos r ON r.id = c.repo_id WHERE c.status IN ('pending', 'error') ORDER BY r.id"
        ).fetchall()

    # ------------------------------------------------------------------ reporting
    def stage_summary(self) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for table in FINAL_STATUSES:
            rows = self.conn.execute(
                f"SELECT status, COUNT(*) AS n FROM {table} GROUP BY status"
            ).fetchall()
            out[table] = {r["status"]: r["n"] for r in rows}
        return out

    def bucket_summary(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT bucket, COUNT(*) AS n, MIN(stars) AS min_stars, MAX(stars) AS max_stars "
            "FROM repos GROUP BY bucket ORDER BY MIN(stars)"
        ).fetchall()
