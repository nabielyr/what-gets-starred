import pytest
import responses

from tests.conftest import API
from wgs.collector.client import GitHubAPIError


def limit_headers(remaining: int, reset: float, resource: str = "core") -> dict:
    return {
        "X-RateLimit-Remaining": str(remaining),
        "X-RateLimit-Reset": str(int(reset)),
        "X-RateLimit-Limit": "5000",
        "X-RateLimit-Resource": resource,
    }


@responses.activate
def test_success_updates_rate_limit_state(client, clock):
    responses.get(f"{API}/repos/a/b", json={"id": 1}, headers=limit_headers(4999, clock.now + 3600))
    resp = client.get("/repos/a/b")
    assert resp.status_code == 200
    assert client.limits["core"].remaining == 4999
    assert clock.sleeps == []
    assert responses.calls[0].request.headers["Authorization"] == "Bearer test-token"


@responses.activate
def test_waits_for_reset_when_quota_is_low(client, clock):
    reset = clock.now + 120
    responses.get(f"{API}/repos/a/b", json={}, headers=limit_headers(3, reset))
    client.get("/repos/a/b")  # leaves 3 remaining, below the threshold of 25
    client.get("/repos/a/b")  # must sleep until the reset first
    assert len(clock.sleeps) == 1
    assert clock.sleeps[0] == pytest.approx(121)


@responses.activate
def test_quota_tracked_per_resource(client, clock):
    responses.get(
        f"{API}/search/repositories", json={}, headers=limit_headers(0, clock.now + 30, "search")
    )
    responses.get(f"{API}/repos/a/b", json={}, headers=limit_headers(4000, clock.now + 3600))
    client.get("/search/repositories", resource="search")
    client.get("/repos/a/b")  # core quota is fine: search exhaustion must not block it
    assert clock.sleeps == []


@responses.activate
def test_primary_rate_limit_403_sleeps_until_reset_and_retries(client, clock):
    reset = clock.now + 60
    responses.get(
        f"{API}/repos/a/b",
        status=403,
        json={"message": "API rate limit exceeded"},
        headers=limit_headers(0, reset),
    )
    responses.get(f"{API}/repos/a/b", json={"ok": True}, headers=limit_headers(4999, reset + 3600))
    resp = client.get("/repos/a/b")
    assert resp.json() == {"ok": True}
    assert clock.sleeps[0] == pytest.approx(61)


@responses.activate
def test_retry_after_header_is_honoured(client, clock):
    responses.get(f"{API}/repos/a/b", status=429, headers={"Retry-After": "17"})
    responses.get(f"{API}/repos/a/b", json={})
    client.get("/repos/a/b")
    assert clock.sleeps == [17]


@responses.activate
def test_secondary_rate_limit_waits_at_least_a_minute(client, clock):
    responses.get(
        f"{API}/repos/a/b",
        status=403,
        json={"message": "You have exceeded a secondary rate limit."},
    )
    responses.get(f"{API}/repos/a/b", json={})
    client.get("/repos/a/b")
    assert clock.sleeps[0] >= 60


@responses.activate
def test_non_rate_limit_403_is_returned(client, clock):
    responses.get(f"{API}/repos/a/b", status=403, json={"message": "Repository access blocked"})
    resp = client.get("/repos/a/b")
    assert resp.status_code == 403
    assert len(responses.calls) == 1
    assert clock.sleeps == []


@responses.activate
def test_server_error_is_retried_with_backoff(client, clock):
    responses.get(f"{API}/repos/a/b", status=502)
    responses.get(f"{API}/repos/a/b", status=503)
    responses.get(f"{API}/repos/a/b", json={"id": 1})
    resp = client.get("/repos/a/b")
    assert resp.status_code == 200
    assert len(clock.sleeps) == 2
    assert clock.sleeps[1] > clock.sleeps[0] - 1  # exponential, allowing for jitter


@responses.activate
def test_gives_up_after_max_retries(client):
    responses.get(f"{API}/repos/a/b", status=500)
    with pytest.raises(GitHubAPIError):
        client.get("/repos/a/b")
    assert len(responses.calls) == client.settings.max_retries + 1


@responses.activate
def test_404_is_returned_without_retry(client, clock):
    responses.get(f"{API}/repos/a/b", status=404, json={"message": "Not Found"})
    assert client.get("/repos/a/b").status_code == 404
    assert len(responses.calls) == 1
