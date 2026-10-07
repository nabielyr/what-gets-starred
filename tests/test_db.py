from wgs.collector.db import Database


def search_item(repo_id: int, stars: int = 50) -> dict:
    return {
        "id": repo_id,
        "full_name": f"owner/repo{repo_id}",
        "stargazers_count": stars,
        "forks_count": 1,
        "open_issues_count": 0,
        "language": "Python",
        "created_at": "2020-01-01T00:00:00Z",
        "pushed_at": "2024-01-01T00:00:00Z",
        "license": {"spdx_id": "MIT"},
        "topics": ["cli", "tool"],
        "archived": False,
    }


def test_insert_repo_is_idempotent(db: Database):
    assert db.insert_repo(search_item(1), "10-100|2020|Python", "10-100", "Python") is True
    assert db.insert_repo(search_item(1), "10-100|2020|Python", "10-100", "Python") is False
    assert db.sampled_count("10-100|2020|Python") == 1
    row = db.conn.execute("SELECT license, topics FROM repos WHERE id = 1").fetchone()
    assert row["license"] == "MIT"
    assert row["topics"] == '["cli", "tool"]'


def test_repos_needing_skips_final_statuses(db: Database):
    for i in (1, 2, 3, 4):
        db.insert_repo(search_item(i), "s", "10-100", "Python")
    db.save_readme(1, "ok", 200, "README.md", 10, "# hi")
    db.save_readme(2, "missing", 404)
    db.save_readme(3, "error", 500)
    todo = [r["id"] for r in db.repos_needing("readmes")]
    assert todo == [3, 4]  # error is retried, missing and ok are final


def test_commit_activity_attempts_accumulate(db: Database):
    db.insert_repo(search_item(1), "s", "10-100", "Python")
    db.save_commit_activity(1, "pending", 202)
    db.save_commit_activity(1, "pending", 202)
    assert db.commit_activity_attempts(1) == 2
    assert [r["id"] for r in db.pending_commit_activity()] == [1]

    weeks = [{"week": 1_700_000_000 + i * 604800, "total": i % 3, "days": [0] * 7} for i in range(52)]
    db.save_commit_activity(1, "ok", 200, weeks)
    row = db.conn.execute("SELECT * FROM commit_activity WHERE repo_id = 1").fetchone()
    assert row["attempts"] == 3
    assert row["total_52w"] == sum(i % 3 for i in range(52))
    assert db.pending_commit_activity() == []


def test_search_cache_round_trip(db: Database):
    assert db.get_search_page("q", 1) is None
    db.save_search_page("q", 1, {"total_count": 2, "incomplete_results": False, "items": [{"id": 1}]})
    cached = db.get_search_page("q", 1)
    assert cached == {"total_count": 2, "incomplete_results": False, "items": [{"id": 1}]}


def test_data_survives_reopening(tmp_path):
    path = tmp_path / "github.db"
    first = Database(path)
    first.insert_repo(search_item(7), "s", "10-100", "Python")
    first.set_meta("snapshot_started", "2026-01-01")
    first.close()

    second = Database(path)
    assert second.has_repo(7)
    assert second.get_meta("snapshot_started") == "2026-01-01"
    second.close()
