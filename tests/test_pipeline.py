"""Discovery and enrichment against a mocked GitHub API."""

import base64
import json
import re
import zlib
from dataclasses import replace
from urllib.parse import parse_qs, urlparse

import responses

from tests.conftest import API
from wgs.collector.discover import init_strata, sample_stratum
from wgs.collector.enrich import run_enrich
from wgs.collector.sampling import Stratum
from wgs.config import Bucket, EnrichSettings, LanguageGroup

STRATUM = Stratum(Bucket("10-100", 10, 99), 2020, LanguageGroup("Python", "language:python"))


def fake_search(total_for):
    """A search endpoint whose results are deterministic per (query, page)."""

    def callback(request):
        params = parse_qs(urlparse(request.url).query)
        query, page = params["q"][0], int(params["page"][0])
        total = total_for(query)
        n = max(0, min(100, min(total, 1000) - (page - 1) * 100))
        items = []
        for i in range(n):
            repo_id = zlib.crc32(f"{query}|{page}|{i}".encode())
            items.append({"id": repo_id, "full_name": f"o/r{repo_id}", "stargazers_count": 42})
        body = {"total_count": total, "incomplete_results": False, "items": items}
        return 200, {}, json.dumps(body)

    return callback


def search_calls():
    return [c for c in responses.calls if "/search/" in c.request.url]


@responses.activate
def test_sample_stratum_meets_quota_and_resumes_without_refetching(client, db, cfg):
    responses.add_callback(responses.GET, f"{API}/search/repositories", fake_search(lambda q: 250))
    init_strata(db, cfg)
    db.update_stratum(STRATUM.key, quota=5)

    assert sample_stratum(client, db, cfg, STRATUM) == 5
    assert db.sampled_count(STRATUM.key) == 5
    first_calls = len(search_calls())

    # Running again with the quota met makes no requests at all.
    assert sample_stratum(client, db, cfg, STRATUM) == 0
    assert len(search_calls()) == first_calls

    # Raising the quota continues from where it stopped, without duplicates.
    db.update_stratum(STRATUM.key, quota=9, status="pending")
    assert sample_stratum(client, db, cfg, STRATUM) == 4
    ids = [r[0] for r in db.conn.execute("SELECT id FROM repos").fetchall()]
    assert len(ids) == len(set(ids)) == 9


@responses.activate
def test_large_strata_are_narrowed_to_a_day_window(client, db, cfg):
    def total_for(query):
        start, end = re.search(r"created:(\S+)\.\.(\S+)", query).groups()
        if start == end:
            return 40  # a single day
        return 1500 if start[5:7] == end[5:7] else 90_000  # a month vs the full year

    responses.add_callback(responses.GET, f"{API}/search/repositories", fake_search(total_for))
    init_strata(db, cfg)
    db.update_stratum(STRATUM.key, quota=3)
    sample_stratum(client, db, cfg, STRATUM)

    row = db.strata("key = ?", (STRATUM.key,))[0]
    assert row["total_count"] == 90_000  # census of the full year is kept
    assert db.sampled_count(STRATUM.key) == 3
    queries = [parse_qs(urlparse(c.request.url).query)["q"][0] for c in search_calls()]
    assert any(re.search(r"created:(\S+)\.\.\1 ", q) for q in queries)


@responses.activate
def test_small_stratum_is_marked_exhausted(client, db, cfg):
    responses.add_callback(responses.GET, f"{API}/search/repositories", fake_search(lambda q: 2))
    init_strata(db, cfg)
    db.update_stratum(STRATUM.key, quota=6)
    assert sample_stratum(client, db, cfg, STRATUM) == 2
    assert db.strata("key = ?", (STRATUM.key,))[0]["status"] == "exhausted"


def add_repo(db, repo_id):
    item = {"id": repo_id, "full_name": f"o/r{repo_id}", "stargazers_count": 42}
    db.insert_repo(item, STRATUM.key, "10-100", "Python")


@responses.activate
def test_enrich_handles_each_endpoint_outcome(client, db, cfg):
    cfg = replace(cfg, enrich=EnrichSettings(stats_max_attempts=3, stats_retry_wait=0))
    add_repo(db, 1)  # healthy repo, stats need one 202 first
    add_repo(db, 2)  # deleted since discovery
    add_repo(db, 3)  # no README, stats never ready

    readme = base64.b64encode("# Title\n\nHello ✨".encode()).decode()
    weeks = [{"week": 1_700_000_000 + i * 604800, "total": 2, "days": [0] * 7} for i in range(52)]

    responses.get(f"{API}/repos/o/r1", json={"id": 1, "subscribers_count": 7, "topics": ["x"]})
    responses.get(f"{API}/repos/o/r1/readme", json={"path": "README.md", "size": 20,
                                                    "encoding": "base64", "content": readme})
    responses.get(f"{API}/repos/o/r1/stats/commit_activity", status=202, json={})
    responses.get(f"{API}/repos/o/r1/stats/commit_activity", json=weeks)

    responses.get(f"{API}/repos/o/r2", status=404, json={"message": "Not Found"})

    responses.get(f"{API}/repos/o/r3", json={"id": 3})
    responses.get(f"{API}/repos/o/r3/readme", status=404, json={})
    responses.get(f"{API}/repos/o/r3/stats/commit_activity", status=202, json={})

    run_enrich(client, db, cfg)

    details = {r["repo_id"]: r for r in db.conn.execute("SELECT * FROM repo_details")}
    assert details[1]["status"] == "ok" and details[1]["subscribers"] == 7
    assert details[2]["status"] == "not_found"

    readmes = {r["repo_id"]: r for r in db.conn.execute("SELECT * FROM readmes")}
    assert readmes[1]["content"] == "# Title\n\nHello ✨"
    assert readmes[2]["status"] == readmes[3]["status"] == "missing"

    stats = {r["repo_id"]: r for r in db.conn.execute("SELECT * FROM commit_activity")}
    assert stats[1]["status"] == "ok" and stats[1]["total_52w"] == 104 and stats[1]["attempts"] == 2
    assert stats[2]["status"] == "not_found"
    assert stats[3]["status"] == "unavailable" and stats[3]["attempts"] == 3

    # Everything is final now: a second run makes no requests.
    before = len(responses.calls)
    run_enrich(client, db, cfg)
    assert len(responses.calls) == before


@responses.activate
def test_one_failing_repo_does_not_stop_the_run(client, db, cfg):
    cfg = replace(cfg, enrich=EnrichSettings(stats_max_attempts=2, stats_retry_wait=0))
    add_repo(db, 1)
    add_repo(db, 2)
    responses.get(f"{API}/repos/o/r1", status=500)  # fails on every retry
    responses.get(f"{API}/repos/o/r1/readme", status=500)
    responses.get(f"{API}/repos/o/r1/stats/commit_activity", status=500)
    responses.get(f"{API}/repos/o/r2", json={"id": 2})
    responses.get(f"{API}/repos/o/r2/readme", status=404, json={})
    responses.get(f"{API}/repos/o/r2/stats/commit_activity", status=204)

    run_enrich(client, db, cfg)

    status = lambda table, i: db.conn.execute(  # noqa: E731
        f"SELECT status FROM {table} WHERE repo_id = ?", (i,)
    ).fetchone()[0]
    assert status("repo_details", 1) == status("readmes", 1) == "error"  # retried next run
    assert status("commit_activity", 1) == "unavailable"  # gave up after max attempts
    assert status("repo_details", 2) == "ok" and status("commit_activity", 2) == "empty"
    assert [r["id"] for r in db.repos_needing("readmes")] == [1]


@responses.activate
def test_hanging_commit_stats_are_given_up_quickly(client, db, cfg):
    import requests

    cfg = replace(cfg, enrich=EnrichSettings(stats_max_attempts=6, stats_retry_wait=0))
    add_repo(db, 1)
    responses.get(f"{API}/repos/o/r1", json={"id": 1})
    responses.get(f"{API}/repos/o/r1/readme", status=404, json={})
    responses.get(f"{API}/repos/o/r1/stats/commit_activity", body=requests.ReadTimeout("slow"))

    run_enrich(client, db, cfg)

    row = db.conn.execute("SELECT * FROM commit_activity WHERE repo_id = 1").fetchone()
    assert row["status"] == "unavailable"
    assert row["attempts"] == 2  # not the full 6
    stats_calls = [c for c in responses.calls if "commit_activity" in c.request.url]
    assert len(stats_calls) == 4  # 2 attempts x (1 try + 1 retry)
