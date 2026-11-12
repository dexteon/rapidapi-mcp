"""The RapidAPI key must never be sent to a host outside *.p.rapidapi.com."""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("RAPIDAPI_CATALOG", str(Path(__file__).parent / "no_catalog.json"))

import server  # noqa: E402


@pytest.mark.parametrize("host", [
    "jsearch.p.rapidapi.com",
    "JSearch.P.RapidAPI.com",
    "active-jobs-db.p.rapidapi.com",
    "a.b-c.p.rapidapi.com",
])
def test_allowed_hosts(host):
    assert server.is_allowed_host(host)


@pytest.mark.parametrize("host", [
    "",
    None,
    "attacker.example",
    "p.rapidapi.com",
    "rapidapi.com",
    "evil.com/x.p.rapidapi.com",
    "evil.com#.p.rapidapi.com",
    "evil.com?.p.rapidapi.com",
    "evil.com@x.p.rapidapi.com",
    "x.p.rapidapi.com.evil.com",
    "x.p.rapidapi.com:8443",
    "x.p.rapidapi.com/",
    "https://x.p.rapidapi.com",
    " x.p.rapidapi.com",
    "x.p.rapidapi.com\n",
    "xp.rapidapi.com",
    "x.p.rapidapi.co",
])
def test_rejected_hosts(host):
    assert not server.is_allowed_host(host)


class _Boom:
    def __init__(self, *a, **k):
        raise AssertionError("no HTTP request may be made for a rejected host")


def test_call_rejects_foreign_host_before_any_request(monkeypatch):
    monkeypatch.setenv("RAPIDAPI_KEY", "test-key-not-real")
    monkeypatch.setattr(server.httpx, "Client", _Boom)
    out = server.rapidapi_call(host="attacker.example", path="/steal")
    assert "error" in out and "host rejected" in out["error"]


def test_call_rejects_foreign_host_from_catalog(monkeypatch):
    monkeypatch.setenv("RAPIDAPI_KEY", "test-key-not-real")
    monkeypatch.setattr(server.httpx, "Client", _Boom)
    monkeypatch.setitem(server.APIS, "bad", {"host": "evil.example.com"})
    out = server.rapidapi_call(slug="bad", path="/x")
    assert "host rejected" in out["error"]


def test_call_allows_rapidapi_host(monkeypatch):
    monkeypatch.setenv("RAPIDAPI_KEY", "test-key-not-real")
    seen = {}

    class _Resp:
        status_code = 200
        headers = {"content-type": "application/json"}
        text = "{}"

        def json(self):
            return {}

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def request(self, method, url, **kw):
            seen["url"] = url
            seen["headers"] = kw["headers"]
            return _Resp()

    monkeypatch.setattr(server.httpx, "Client", _Client)
    out = server.rapidapi_call(host="jsearch.p.rapidapi.com", path="search")
    assert out["status"] == 200
    assert seen["url"] == "https://jsearch.p.rapidapi.com/search"
    assert seen["headers"]["x-rapidapi-host"] == "jsearch.p.rapidapi.com"
