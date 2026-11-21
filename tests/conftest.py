"""Unit tests never touch the network: every HTTP call goes to a mock transport."""

import os
import sys

import httpx
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

KEY = "sk_test_unit_key_0123456789abcdef"


class FakeApi:
    """Answers with `respond(request)` and records every request."""

    def __init__(self):
        self.requests = []
        self.respond = lambda request: httpx.Response(500)

    def __call__(self, request):
        self.requests.append(request)
        return self.respond(request)


@pytest.fixture
def api(monkeypatch):
    fake = FakeApi()
    original = httpx.Client

    def client_with_mock(**kwargs):
        kwargs["transport"] = httpx.MockTransport(fake)
        return original(**kwargs)

    monkeypatch.setattr(httpx, "Client", client_with_mock)
    return fake


@pytest.fixture
def key():
    return KEY
