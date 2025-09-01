import os
os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"

import asyncio
from types import SimpleNamespace

import pytest
from web3 import Web3

import web3auth_provider
import evm_pay


def test_web3auth_tx_to_tier(monkeypatch):
    async def run():
        # simulate Web3Auth authentication
        monkeypatch.setenv("WEB3AUTH_CLIENT_ID", "CID")
        monkeypatch.setenv("WEB3AUTH_PRIVATE_KEY", "PRIV")
        monkeypatch.setitem(web3auth_provider.INFURA_RPC, "ethereum", "http://rpc.local")

        called = {}

        class DummySDK:
            def __init__(self, client_id, private_key, rpc_target):
                called["client_id"] = client_id
                called["private_key"] = private_key
                called["rpc_target"] = rpc_target
            def get_provider(self):
                called["used"] = True
                return "http://auth.rpc"

        monkeypatch.setattr(web3auth_provider, "Web3Auth", DummySDK)

        provider = await asyncio.to_thread(web3auth_provider.get_provider, "ethereum")
        assert provider.endpoint_uri == "http://auth.rpc"
        assert called["client_id"] == "CID"
        assert called["private_key"] == "PRIV"
        assert called["used"] is True

        # mock transaction fetch and USD conversion
        value_wei = Web3.to_wei(0.02, "ether")  # 0.02 ETH

        class DummyW3:
            def __init__(self):
                self.eth = SimpleNamespace(
                    get_transaction=lambda h: {"to": evm_pay.RECEIVING_ADDRESS, "value": value_wei},
                    get_transaction_receipt=lambda h: {"logs": [], "blockNumber": 1},
                    block_number=1,
                )
            @staticmethod
            def from_wei(v, unit):
                return Web3.from_wei(v, unit)

        monkeypatch.setattr(evm_pay, "get_web3", lambda chain, rpc_url="": DummyW3())
        async def fake_price(chain):
            return 2000.0

        monkeypatch.setattr(evm_pay, "_coingecko_native_price", fake_price)

        info = await evm_pay.fetch_tx_on_chain("ethereum", "", "0x" + "1"*64)
        assert info["usd_total"] == pytest.approx(40.0)

        tier = evm_pay.pick_tier(info["usd_total"])
        assert tier == "pro"

    asyncio.run(run())
