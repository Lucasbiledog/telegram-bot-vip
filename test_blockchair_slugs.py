import asyncio
import importlib
import os
import logging

import httpx
import pytest

os.environ.setdefault("BOT_TOKEN", "123:ABC")
os.environ.setdefault("DATABASE_URL", "sqlite:///test.db")
os.environ.setdefault("AUTO_APPROVE_CRYPTO", "0")
os.environ.setdefault("WEBHOOK_URL", "http://localhost")

import main
importlib.reload(main)


class DummyResponse:
    status_code = 404

    def json(self):
        return {}


class DummyClient:
    def __init__(self, *args, **kwargs):
        self.urls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        pass

    async def get(self, url):
        self.urls.append(url)
        return DummyResponse()


def test_verify_tx_blockchair_supported_networks(monkeypatch):
    dummy_client = DummyClient()
    monkeypatch.setattr(httpx, "AsyncClient", lambda timeout=10: dummy_client)
    tx_hash = "0x" + "0" * 64
    asyncio.run(main.verify_tx_blockchair(tx_hash))
    expected = set(main.BLOCKCHAIR_SLUGS.keys())


    called = {url.split("/")[3] for url in dummy_client.urls}
    assert called == expected


class ListResponse:
    status_code = 200

    def json(self):
        return {"data": []}


class ListClient:
    def __init__(self, *args, **kwargs):
        self.urls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        pass

    async def get(self, url):
        self.urls.append(url)
        return ListResponse()


def test_verify_tx_blockchair_list_data(monkeypatch, caplog):
    client = ListClient()
    monkeypatch.setattr(httpx, "AsyncClient", lambda timeout=10: client)
    tx_hash = "0x" + "1" * 64
    with caplog.at_level(logging.WARNING):
        res = asyncio.run(main.verify_tx_blockchair(tx_hash))
    assert res["ok"] is False
    assert len(client.urls) == len(main.BLOCKCHAIR_SLUGS)
    assert "Blockchair" in caplog.text