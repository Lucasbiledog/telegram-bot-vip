"""Utilities for loading chain data from Web3Auth."""
from __future__ import annotations

from typing import Dict, Any, Iterable

import asyncio
import httpx

WEB3AUTH_CHAIN_URL = "https://rpc.web3auth.io/chain-config"


async def load_web3auth_chains() -> Dict[str, Dict[str, Any]]:
    """Fetch the official Web3Auth chain configuration list asynchronously.

    Returns a mapping keyed by chain name. Each entry provides:
    ``chain_id`` (int), ``symbol`` (str) and ``rpc`` (str) when
    available. A :class:`RuntimeError` is raised if the download or
    parsing fails.
    """
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(WEB3AUTH_CHAIN_URL, timeout=10)
        response.raise_for_status()
        data = response.json()
    except Exception as exc:  # pragma: no cover - network failure path
        raise RuntimeError("Failed to download Web3Auth chain list") from exc

    chains: Dict[str, Dict[str, Any]] = {}

    iterable: Iterable[Any]
    if isinstance(data, list):
        iterable = data
    elif isinstance(data, dict):
        if isinstance(data.get("chains"), list):
            iterable = data["chains"]
        elif isinstance(data.get("result"), dict):
            iterable = data["result"].values()
        else:
            iterable = data.values()
    else:
        iterable = []

    for item in iterable:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("chainName") or item.get("displayName")
        if not name:
            continue
        chain_id = item.get("chainId") or item.get("chain_id")
        if isinstance(chain_id, str):
            try:
                chain_id = int(chain_id, 0)
            except ValueError:
                continue
        if not isinstance(chain_id, int):
            continue
        symbol = (
            item.get("ticker")
            or item.get("symbol")
            or item.get("nativeCurrency", {}).get("symbol", "")
        )
        rpc = item.get("rpcTarget") or item.get("rpc") or item.get("rpcUrl") or ""
        chains[name] = {"chain_id": chain_id, "symbol": symbol, "rpc": rpc}

    if not chains:
        raise RuntimeError("Web3Auth chain list empty or unrecognized format")
    return chains


def load_web3auth_chains_sync() -> Dict[str, Dict[str, Any]]:
    """Synchronous wrapper around :func:`load_web3auth_chains`.

    Useful for contexts where awaiting is not possible, such as module
    import time. The call still performs asynchronous HTTP operations
    under the hood but blocks until completion.
    """

    return asyncio.run(load_web3auth_chains())
