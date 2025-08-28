+74
-0

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

class DummyClient:
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
            return DummyResponse({"result": "0x12"})
        return DummyResponse({})

def test_verify_erc20_payment_bscscan(monkeypatch):
    client = DummyClient()
    monkeypatch.setattr(httpx, "AsyncClient", lambda timeout=10: client)
    cfg = {
        "wallet_address": WALLET,
        "token_contract": CONTRACT,
        "bscscan_api_key": "KEY",
    }
    tx_hash = "0x" + "1"*64
    res = asyncio.run(main.verify_erc20_payment_bscscan(cfg, tx_hash))
    assert res["ok"] is True
    assert res["amount_raw"] == 5
    assert res["confirmations"] == 16
    actions = [p[1]["action"] for p in client.calls]
    assert set(["eth_getTransactionReceipt", "eth_blockNumber", "eth_call"]).issubset(actions)