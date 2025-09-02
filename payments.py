from __future__ import annotations
import logging, os
from typing import Optional, Tuple, Dict, Any
from web3 import Web3
import httpx

LOG = logging.getLogger("payments")

WALLET_ADDRESS = (os.getenv("WALLET_ADDRESS") or "").strip()
MIN_CONFIRMATIONS = int(os.getenv("MIN_CONFIRMATIONS", "1"))   # aumente em prod
DEBUG_PAYMENTS = os.getenv("DEBUG_PAYMENTS", "0") == "1"
ALLOW_ANY_TO   = os.getenv("ALLOW_ANY_TO", "0") == "1"         # apenas para testes

# --------- Tabela multi-chain ----------
# chainId(hex) -> { rpc, sym (nativo), cg_native (id CoinGecko), cg_platform (slug)}
CHAINS: Dict[str, Dict[str, str]] = {
    "0x1":    {"rpc":"https://rpc.ankr.com/eth",              "sym":"ETH",  "cg_native":"ethereum",      "cg_platform":"ethereum"},
    "0x38":   {"rpc":"https://bsc-dataseed.binance.org",      "sym":"BNB",  "cg_native":"binancecoin",   "cg_platform":"binance-smart-chain"},
    "0x89":   {"rpc":"https://polygon-rpc.com",               "sym":"MATIC","cg_native":"polygon-pos",   "cg_platform":"polygon-pos"},
    "0xa4b1": {"rpc":"https://arb1.arbitrum.io/rpc",          "sym":"ETH",  "cg_native":"ethereum",      "cg_platform":"arbitrum-one"},
    "0xa":    {"rpc":"https://mainnet.optimism.io",           "sym":"ETH",  "cg_native":"ethereum",      "cg_platform":"optimistic-ethereum"},
    "0x2105": {"rpc":"https://mainnet.base.org",              "sym":"ETH",  "cg_native":"ethereum",      "cg_platform":"base"},
    "0xa86a": {"rpc":"https://api.avax.network/ext/bc/C/rpc", "sym":"AVAX", "cg_native":"avalanche-2",   "cg_platform":"avalanche"},
    "0xfa":   {"rpc":"https://rpc.ftm.tools",                 "sym":"FTM",  "cg_native":"fantom",        "cg_platform":"fantom"},
    "0xe708": {"rpc":"https://rpc.linea.build",               "sym":"ETH",  "cg_native":"ethereum",      "cg_platform":"linea"},
    "0x144":  {"rpc":"https://mainnet.era.zksync.io",         "sym":"ETH",  "cg_native":"ethereum",      "cg_platform":"zksync"},
}

ERC20_TRANSFER_SIG = Web3.keccak(text="Transfer(address,address,uint256)").hex()

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
    try:
        tx = w3.eth.get_transaction(tx_hash)
    except Exception:
        return False, "Transação não encontrada.", None, {}

    receipt = None
    if tx.get("blockHash"):
        try:
            receipt = w3.eth.get_transaction_receipt(tx_hash)
        except Exception:
            pass

    confirmations = await _get_confirmations(w3, tx.get("blockNumber"))
    if confirmations < MIN_CONFIRMATIONS:
        return False, f"Aguardando confirmações: {confirmations}/{MIN_CONFIRMATIONS}", None, {"confirmations": confirmations}

    if receipt and receipt.get("status") != 1:
        return False, "Transação revertida.", None, {"confirmations": confirmations}

    details: Dict[str, Any] = {"chain_id": chain_id, "confirmations": confirmations}
    to_addr = (tx.get("to") or "").lower()

    # Nativo
    if WALLET_ADDRESS and to_addr == WALLET_ADDRESS.lower():
        value_wei = int(tx["value"])
        amount_native = float(value_wei) / float(10 ** 18)
        px = await _usd_native(chain_id, amount_native)
        if not px:
            return False, "Preço USD indisponível (nativo).", None, details
        price_usd, paid_usd = px
        sym = CHAINS[chain_id]["sym"]
        details.update({"type":"native","token_symbol":sym,"amount_human":amount_native,"price_usd":price_usd,"paid_usd":paid_usd})
        return True, f"{sym} nativo OK em {chain_id}: ${paid_usd:.2f}", paid_usd, details

    # ERC-20 (analisa logs Transfer para a nossa carteira)
    if not receipt:
        return False, "Receipt indisponível para token.", None, details

    found_value_raw = None
    found_token_addr = None
    found_to = None

    for log in receipt.get("logs", []):
        addr = (log.get("address") or "").lower()
        topics = log.get("topics") or []
        if len(topics) < 3:
            continue
        t0 = topics[0].hex().lower() if hasattr(topics[0], "hex") else str(topics[0]).lower()
        if t0 != ERC20_TRANSFER_SIG.lower():
            continue
        t2 = topics[2].hex() if hasattr(topics[2], "hex") else str(topics[2])
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

    # fallback de teste
    if found_value_raw is None or not found_token_addr:
        if ALLOW_ANY_TO and receipt and receipt.get("logs"):
            # pega o primeiro Transfer que aparecer
            for log in receipt["logs"]:
                topics = log.get("topics") or []
                if len(topics) < 3:
                    continue
                t0 = topics[0].hex().lower() if hasattr(topics[0], "hex") else str(topics[0]).lower()
                if t0 != ERC20_TRANSFER_SIG.lower():
                    continue
                try:
                    data_hex = log.get("data")
                    found_value_raw = int(data_hex, 16)
                    found_token_addr = Web3.to_checksum_address((log.get("address") or "0x0"))
                    break
                except Exception:
                    pass

    if found_value_raw is None or not found_token_addr:
        msg = "Nenhuma transferência válida p/ a carteira destino."
        if DEBUG_PAYMENTS:
            msg = (msg +
                   f"\n[DEBUG] chain={chain_id} tx.to={to_addr} expected={WALLET_ADDRESS.lower() if WALLET_ADDRESS else '-'}")
        return False, msg, None, details

    decimals = _erc20_decimals(w3, found_token_addr)
    symbol = _erc20_symbol(w3, found_token_addr) or "TOKEN"
    px = await _usd_token(chain_id, found_token_addr, found_value_raw, decimals)
    if not px:
        return False, "Preço USD indisponível (token).", None, details
    price_usd, paid_usd = px
    amount_human = float(found_value_raw) / float(10 ** decimals)

    details.update({
        "type":"erc20","token_address":found_token_addr,"token_symbol":symbol,
        "amount_human":amount_human,"price_usd":price_usd,"paid_usd":paid_usd, "to": found_to
    })
    return True, f"Token {symbol} OK em {chain_id}: ${paid_usd:.2f}", paid_usd, details

async def resolve_payment_usd_autochain(tx_hash: str) -> Tuple[bool, str, Optional[float], Dict[str, Any]]:
    # percorre as chains definidas e tenta achar a tx
    for chain_id, meta in CHAINS.items():
        w3 = _w3(meta["rpc"])
        try:
            _ = w3.eth.get_transaction(tx_hash)
        except Exception:
            continue
        ok, msg, usd, details = await _resolve_on_chain(w3, chain_id, tx_hash)
        # anexa nome da rede
        details["chain_name"] = chain_id
        return ok, msg, usd, details
    return False, "Transação não encontrada nas chains suportadas.", None, {}
