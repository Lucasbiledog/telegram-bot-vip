import os
os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
import asyncio
import importlib
import httpx

os.environ.setdefault("BOT_TOKEN", "123:ABC")
os.environ.setdefault("DATABASE_URL", "sqlite:///test.db")
os.environ.setdefault("WEBHOOK_URL", "http://localhost")

import main
importlib.reload(main)

WALLET = "0x40ddbd27f878d07808339f9965f013f1cbc2f812"
CONTRACT = "0x7130d2a12b9bcbfae4f2634d864a1ee1ce3ead9c"

class DummyResponse:
    def __init__(self, data):
        self._data = data
        self.status_code = 200
    def json(self):
        return self._data
    def raise_for_status(self):
        pass

class DummyClientToken:
    def __init__(self, *args, **kwargs):
        self.calls = []
    async def __aenter__(self):
        return self
    async def __aexit__(self, exc_type, exc, tb):
        pass
    async def get(self, url, params):
        self.calls.append((url, params))
        action = params.get("action")
        if action == "eth_getTransactionReceipt":
            return DummyResponse({
                "result": {
                    "status": "0x1",
                    "blockNumber": "0x10",
                    "logs": [
                        {
                            "address": CONTRACT,
                            "topics": [
                                main.TRANSFER_TOPIC,
                                "0x" + "0"*64,
                                "0x" + "0"*24 + WALLET[2:],
                            ],
                            "data": "0x5",
                        }
                    ],
                }
            })
        if action == "eth_blockNumber":
            return DummyResponse({"result": "0x20"})
        if action == "eth_call":
            return DummyResponse({"result": "0x0"})
        return DummyResponse({})

class DummyClientNative:
    def __init__(self, *args, **kwargs):
        self.calls = []
    async def __aenter__(self):
        return self
    async def __aexit__(self, exc_type, exc, tb):
        pass
    async def get(self, url, params):
        self.calls.append((url, params))
        action = params.get("action")
        if action == "eth_getTransactionReceipt":
            return DummyResponse({
                "result": {
                    "status": "0x1",
                    "blockNumber": "0x10",
                    "logs": [],
                }
            })
        if action == "eth_blockNumber":
            return DummyResponse({"result": "0x20"})
        if action == "eth_getTransactionByHash":
            return DummyResponse({
                "result": {
                    "to": WALLET,
                    "from": "0x" + "1"*40,
                    "value": hex(70),
                }
            })
        return DummyResponse({})

def test_verify_tx_bscscan_token(monkeypatch):
    client = DummyClientToken()
    monkeypatch.setattr(httpx, "AsyncClient", lambda timeout=10: client)
    async def fake_price(addr):
        return (14.0, 0)
    monkeypatch.setattr(main, "fetch_price_usd_for_contract", fake_price)
    cfg = {
        "wallet_address": WALLET,
        "bscscan_api_key": "KEY",
        "chain_name": "Binance Smart Chain",
    }
    tx_hash = "0x" + "1"*64
    res = asyncio.run(main.verify_tx_bscscan(cfg, tx_hash))
    assert res["ok"] is True
    assert res["amount_usd"] == 70.0
    assert res["plan_days"] == 90
    actions = [p[1]["action"] for p in client.calls]
    assert set(["eth_getTransactionReceipt", "eth_blockNumber"]).issubset(actions)

def test_verify_tx_bscscan_native(monkeypatch):
    client = DummyClientNative()
    monkeypatch.setattr(httpx, "AsyncClient", lambda timeout=10: client)
    async def fake_native_price(cfg):
        return 1e18
    monkeypatch.setattr(main, "fetch_price_usd", fake_native_price)
    cfg = {
        "wallet_address": WALLET,
        "bscscan_api_key": "KEY",
        "chain_name": "Binance Smart Chain",
    }
    tx_hash = "0x" + "2"*64
    res = asyncio.run(main.verify_tx_bscscan(cfg, tx_hash))
    assert res["ok"] is True
    assert res["amount_usd"] == 70.0
    assert res["plan_days"] == 90
    actions = [p[1]["action"] for p in client.calls]
    assert set(["eth_getTransactionReceipt", "eth_blockNumber", "eth_getTransactionByHash"]).issubset(actions)


def test_verify_tx_bscscan_token_below_min(monkeypatch):
    client = DummyClientToken()
    monkeypatch.setattr(httpx, "AsyncClient", lambda timeout=10: client)

    async def fake_price(addr):
        return (14.0, 0)

    monkeypatch.setattr(main, "fetch_price_usd_for_contract", fake_price)
    monkeypatch.setattr(main, "MIN_TOKEN_AMOUNT", 10)

    cfg = {
        "wallet_address": WALLET,
        "bscscan_api_key": "KEY",
        "chain_name": "Binance Smart Chain",
    }
    tx_hash = "0x" + "1" * 64
    res = asyncio.run(main.verify_tx_bscscan(cfg, tx_hash))
    assert res["ok"] is False
    assert "Quantidade de token abaixo do mínimo" in res["reason"]
