import pytest
import responses

from wgs.collector.star_history import (
    ArchiveClient,
    ArchiveError,
    build_query,
    parse_tsv,
    run_star_history,
)
from wgs.config import StarHistorySettings

URL = "https://play.clickhouse.com/"


def add_repo(db, repo_id, name, bucket="1k-10k", current_name=None, status="ok"):
    db.insert_repo({"id": repo_id, "full_name": name, "stargazers_count": 2000}, "s", bucket, "Go")
    db.save_details(repo_id, status, 200, {"full_name": current_name or name} if status == "ok" else None)


@pytest.fixture
def archive():
    return ArchiveClient(StarHistorySettings(max_retries=2), sleep=lambda s: None)


def test_build_query_is_case_insensitive_and_escaped():
    sql = build_query(["Owner/Repo", "owner/repo", "o/it's"], before="2026-10-08 16:00:00")
    assert "lower(repo_name) IN ('o/it\\'s', 'owner/repo')" in sql
    assert "created_at < toDateTime('2026-10-08 16:00:00')" in sql
    assert "event_type = 'WatchEvent'" in sql


def test_parse_tsv():
    rows = parse_tsv("a/b\t2020-01-01\t3\na/b\t2020-01-02\t1\nc/d\t2021-05-05\t7\n")
    assert rows == {"a/b": [("2020-01-01", 3), ("2020-01-02", 1)], "c/d": [("2021-05-05", 7)]}


@responses.activate
def test_run_star_history_merges_renamed_repos_and_resumes(db, cfg, archive):
    add_repo(db, 1, "old-org/tool", current_name="new-org/Tool")  # renamed after discovery
    add_repo(db, 2, "quiet/repo")  # no events in the archive
    add_repo(db, 3, "small/repo", bucket="10-100")  # bucket not configured: skipped
    add_repo(db, 4, "gone/repo", status="not_found")  # deleted: skipped
    db.set_meta("snapshot_started", "2026-10-08T16:00:00+00:00")
    responses.post(
        URL,
        body="new-org/tool\t2024-01-02\t5\nold-org/tool\t2024-01-01\t2\nold-org/tool\t2024-01-02\t1\n",
    )

    run_star_history(db, cfg, archive)

    sql = responses.calls[0].request.body.decode()
    assert "'new-org/tool', 'old-org/tool', 'quiet/repo'" in sql
    assert "small/repo" not in sql and "gone/repo" not in sql
    assert "2026-10-08 16:00:00" in sql

    daily = db.conn.execute("SELECT day, stars FROM star_daily WHERE repo_id = 1 ORDER BY day").fetchall()
    assert [tuple(r) for r in daily] == [("2024-01-01", 2), ("2024-01-02", 6)]
    history = {r["repo_id"]: r for r in db.conn.execute("SELECT * FROM star_history")}
    assert history[1]["status"] == "ok" and history[1]["events"] == 8
    assert history[1]["first_day"] == "2024-01-01" and history[1]["last_day"] == "2024-01-02"
    assert history[2]["status"] == "no_events" and history[2]["events"] == 0
    assert set(history) == {1, 2}

    run_star_history(db, cfg, archive)  # everything stored: no new queries
    assert len(responses.calls) == 1


@responses.activate
def test_archive_retries_server_errors(archive):
    responses.post(URL, status=503)
    responses.post(URL, body="a/b\t2020-01-01\t1\n")
    assert archive.query("SELECT 1") == "a/b\t2020-01-01\t1\n"
    assert len(responses.calls) == 2


@responses.activate
def test_archive_bad_query_fails_fast(archive):
    responses.post(URL, status=400, body="Syntax error")
    with pytest.raises(ArchiveError, match="Syntax error"):
        archive.query("SELEC 1")
    assert len(responses.calls) == 1


@responses.activate
def test_archive_gives_up_after_retries(archive):
    responses.post(URL, status=500)
    with pytest.raises(ArchiveError):
        archive.query("SELECT 1")
    assert len(responses.calls) == 3
