import pytest

from wgs.collector.client import GitHubClient
from wgs.collector.db import Database
from wgs.config import GitHubSettings, load_config

API = "https://api.github.com"


class FakeClock:
    """Records sleeps instead of sleeping, and advances time accordingly."""

    def __init__(self, now: float = 1_000_000.0):
        self.now = now
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def client(clock):
    settings = GitHubSettings(max_retries=3, backoff_base=2.0, backoff_max=10)
    return GitHubClient("test-token", settings, sleep=clock.sleep, clock=clock.time)


@pytest.fixture
def db():
    database = Database(":memory:")
    yield database
    database.close()


@pytest.fixture
def cfg():
    return load_config()
