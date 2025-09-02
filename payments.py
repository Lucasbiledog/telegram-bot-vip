# payments.py
from __future__ import annotations

import os
import logging
from typing import Optional, Tuple, Dict, Any, List

from web3 import Web3
import httpx

LOG = logging.getLogger("payments")

# topo do arquivo (com os imports)
import logging, os
from typing import Optional, Tuple, Dict, Any
from web3 import Web3
import httpx

LOG = logging.getLogger("payments")

DEBUG_PAYMENTS = os.getenv("DEBUG_PAYMENTS", "0") == "1"
ALLOW_ANY_TO   = os.getenv("ALLOW_ANY_TO", "0") == "1"

# carteira destino (obrigatório)
WALLET_ADDRESS = (os.getenv("WALLET_ADDRESS") or "").strip()
MIN_CONFIRMATIONS = int(os.getenv("MIN_CONFIRMATIONS", "1"))  # 1 em teste

# nome bonitinho das chains (só para mensagens)
CHAIN_NAMES: Dict[str, str] = {
    "0x1": "Ethereum",
    "0x38": "BNB Smart Chain",
    "0x89": "Polygon",
    "0xa4b1": "Arbitrum One",
    "0xa": "OP Mainnet",
    "0x2105": "Base",
    "0xa86a": "Avalanche",
    "0xfa": "Fantom",
    "0xe708": "Linea",
    "0x144": "zkSync Era",
}

# tabela de RPC e metadados (como você já tinha)
CHAINS: Dict[str, Dict[str, str]] = {
    "0x1":    {"rpc":"https://rpc.ankr.com/eth",            "sym":"ETH",  "cg_native":"ethereum",      "cg_platform":"ethereum"},
    "0x38":   {"rpc":"https://bsc-dataseed.binance.org",    "sym":"BNB",  "cg_native":"binancecoin",   "cg_platform":"binance-smart-chain"},
    "0x89":   {"rpc":"https://polygon-rpc.com",             "sym":"MATIC","cg_native":"polygon-pos",   "cg_platform":"polygon-pos"},
    "0xa4b1": {"rpc":"https://arb1.arbitrum.io/rpc",        "sym":"ETH",  "cg_native":"ethereum",      "cg_platform":"arbitrum-one"},
    "0xa":    {"rpc":"https://mainnet.optimism.io",         "sym":"ETH",  "cg_native":"ethereum",      "cg_platform":"optimistic-ethereum"},
    "0x2105": {"rpc":"https://mainnet.base.org",            "sym":"ETH",  "cg_native":"ethereum",      "cg_platform":"base"},
    "0xa86a": {"rpc":"https://api.avax.network/ext/bc/C/rpc","sym":"AVAX","cg_native":"avalanche-2",  "cg_platform":"avalanche"},
    "0xfa":   {"rpc":"https://rpc.ftm.tools",               "sym":"FTM",  "cg_native":"fantom",        "cg_platform":"fantom"},
    "0xe708": {"rpc":"https://rpc.linea.build",             "sym":"ETH",  "cg_native":"ethereum",      "cg_platform":"linea"},
    "0x144":  {"rpc":"https://mainnet.era.zksync.io",       "sym":"ETH",  "cg_native":"ethereum",      "cg_platform":"zksync"},
}

ERC20_TRANSFER_SIG = Web3.keccak(text="Transfer(address,address,uint256)").hex()

def _fmt_usd(x: float) -> str:
    # evita $0.00 para valores pequenos
    if x < 0.01:
        return f"${x:.4f}"
    return f"${x:.2f}"

def _chain_name(chain_id: str) -> str:
    return CHAIN_NAMES.get(chain_id, chain_id)

def _w3(rpc: str) -> Web3:
    return Web3(Web3.HTTPProvider(rpc))

def _topic_addr(topic_hex: str) -> str:
    return Web3.to_checksum_address("0x" + topic_hex[-40:])

async def _get_confirmations(w3: Web3, block_number: Optional[int]) -> int:
    if block_number is None:
        return 0
    latest = w3.eth.block_number
    return max(0, latest - block_number)

async def _cg_get(url: str) -> Optional[dict]:
    try:
        async with httpx.AsyncClient(timeout=12) as cli:
            r = await cli.get(url)
            r.raise_for_status()
            return r.json()
    except Exception:
        return None

async def _usd_native(chain_id: str, amount_native: float) -> Optional[Tuple[float, float]]:
    cg_id = CHAINS[chain_id]["cg_native"]
    data = await _cg_get(f"https://api.coingecko.com/api/v3/simple/price?ids={cg_id}&vs_currencies=usd")
    if not data or cg_id not in data or "usd" not in data[cg_id]:
        return None
    px = float(data[cg_id]["usd"])
    return px, amount_native * px

async def _usd_token(chain_id: str, token_addr: str, amount_raw: int, decimals: int) -> Optional[Tuple[float, float]]:
    platform = CHAINS[chain_id]["cg_platform"]
    token_lc = token_addr.lower()
    data = await _cg_get(
        f"https://api.coingecko.com/api/v3/simple/token_price/{platform}"
        f"?contract_addresses={token_lc}&vs_currencies=usd"
    )
    if not data:
        return None
    for k, v in data.items():
        if k.lower() == token_lc and "usd" in v:
            px = float(v["usd"])
            amt = float(amount_raw) / float(10 ** decimals)
            return px, amt * px
    return None

def _erc20_static_call(w3: Web3, token: str, sig4: str) -> Optional[bytes]:
    try:
        return w3.eth.call({"to": token, "data": sig4})
    except Exception:
        return None

def _erc20_decimals(w3: Web3, token: str) -> int:
    raw = _erc20_static_call(w3, token, "0x313ce567")  # decimals()
    if not raw or len(raw) < 32:
        return 18
    return int.from_bytes(raw[-32:], "big")

def _erc20_symbol(w3: Web3, token: str) -> str:
    raw = _erc20_static_call(w3, token, "0x95d89b41")  # symbol()
    if not raw:
        return "TOKEN"
    try:
        if len(raw) >= 96 and raw[:4] == b"\x00\x00\x00\x20":
            strlen = int.from_bytes(raw[64:96], "big")
            return raw[96:96+strlen].decode("utf-8", errors="ignore") or "TOKEN"
        return raw.rstrip(b"\x00").decode("utf-8", errors="ignore") or "TOKEN"
    except Exception:
        return "TOKEN"

async def _resolve_on_chain(w3: Web3, chain_id: str, tx_hash: str) -> Tuple[bool, str, Optional[float], Dict[str, Any]]:
    # estrutura base do details (debug)
    base_details: Dict[str, Any] = {
        "chain_id": chain_id,
        "chain_name": _chain_name(chain_id),
        "wallet_expected": WALLET_ADDRESS,
        "tx_hash": tx_hash,
        "mode": None,  # "native" ou "erc20"
    }

    try:
        tx = w3.eth.get_transaction(tx_hash)
    except Exception:
        return False, "Transação não encontrada.", None, base_details

    receipt = None
    if tx.get("blockHash"):
        try:
            receipt = w3.eth.get_transaction_receipt(tx_hash)
        except Exception:
            pass

    confirmations = await _get_confirmations(w3, tx.get("blockNumber"))
    base_details["confirmations"] = confirmations
    if confirmations < MIN_CONFIRMATIONS:
        return False, f"Aguardando confirmações: {confirmations}/{MIN_CONFIRMATIONS}", None, base_details

    if receipt and receipt.get("status") != 1:
        return False, "Transação revertida.", None, base_details

    to_addr = (tx.get("to") or "").lower()
    frm_addr = (tx.get("from") or "").lower()
    base_details["tx_from"] = frm_addr
    base_details["tx_to"] = to_addr

    # Nativo
    if WALLET_ADDRESS and to_addr == WALLET_ADDRESS.lower() and int(tx.get("value", 0)) > 0:
        base_details["mode"] = "native"
        value_wei = int(tx["value"])
        amount_native = float(value_wei) / float(10 ** 18)
        px = await _usd_native(chain_id, amount_native)
        if not px:
            return False, "Preço USD indisponível (nativo).", None, base_details
        price_usd, paid_usd = px
        sym = CHAINS[chain_id]["sym"]
        base_details.update({
            "token_symbol": sym,
            "amount_human": amount_native,
            "price_usd": price_usd,
            "paid_usd": paid_usd,
        })
        msg = (
            f"Pagamento detectado: {sym} nativo em {_chain_name(chain_id)}\n"
            f"Quantidade: {amount_native:.8f} {sym}\n"
            f"Preço {sym}: ${price_usd:.2f}\n"
            f"Total em USD: {_fmt_usd(paid_usd)}"
        )
        return True, msg, paid_usd, base_details

    # ERC-20 (precisa do receipt para ler logs)
    if not receipt:
        return False, "Receipt indisponível para token.", None, base_details

    found_value_raw = None
    found_token_addr = None
    found_to = None

    for log in receipt.get("logs", []):
        addr = (log.get("address") or "").lower()
        topics = log.get("topics") or []
        if len(topics) < 3:
            continue
        t0 = topics[0].hex().lower() if hasattr(topics[0], 'hex') else str(topics[0]).lower()
        if t0 != ERC20_TRANSFER_SIG.lower():
            continue
        t2 = topics[2].hex() if hasattr(topics[2], 'hex') else str(topics[2])
        try:
            toA = _topic_addr(t2)
        except Exception:
            continue
        if WALLET_ADDRESS and toA.lower() != WALLET_ADDRESS.lower():
            continue
        try:
            data_hex = log.get("data")
            value_raw = int(data_hex, 16)
        except Exception:
            continue
        found_value_raw = value_raw
        found_token_addr = Web3.to_checksum_address(addr)
        found_to = toA
        break

    base_details["erc20_to"] = found_to
    base_details["erc20_token"] = found_token_addr

    if found_value_raw is None or not found_token_addr:
        if ALLOW_ANY_TO:
            LOG.warning("ALLOW_ANY_TO ativo - aceitando tx sem conferir destino")
        else:
            return False, "Nenhuma transferência válida p/ a carteira destino.", None, base_details

    base_details["mode"] = "erc20"
    decimals = _erc20_decimals(w3, found_token_addr)
    symbol = _erc20_symbol(w3, found_token_addr) or "TOKEN"
    px = await _usd_token(chain_id, found_token_addr, found_value_raw, decimals)
    if not px:
        return False, "Preço USD indisponível (token).", None, base_details
    price_usd, paid_usd = px
    amount_human = float(found_value_raw) / float(10 ** decimals)

    base_details.update({
        "token_symbol": symbol,
        "amount_human": amount_human,
        "price_usd": price_usd,
        "paid_usd": paid_usd,
    })

    msg = (
        f"Pagamento detectado: {symbol} (ERC-20) em {_chain_name(chain_id)}\n"
        f"Quantidade: {amount_human:.8f} {symbol}\n"
        f"Preço {symbol}: ${price_usd:.2f}\n"
        f"Total em USD: {_fmt_usd(paid_usd)}"
    )
    return True, msg, paid_usd, base_details


async def resolve_payment_usd_autochain(tx_hash: str) -> Tuple[bool, str, Optional[float], Dict[str, Any]]:
    """Tenta localizar a tx percorrendo as chains configuradas."""
    for chain_id, meta in CHAINS.items():
        w3 = _w3(meta["rpc"])
        try:
            _ = w3.eth.get_transaction(tx_hash)
        except Exception:
            continue
        ok, msg, usd, details = await _resolve_on_chain(w3, chain_id, tx_hash)
        if DEBUG_PAYMENTS:
            LOG.info("[resolve] chain=%s ok=%s usd=%s details=%s", chain_id, ok, usd, details)
        return ok, msg, usd, details
    return False, "Transação não encontrada nas chains suportadas.", None, {}
