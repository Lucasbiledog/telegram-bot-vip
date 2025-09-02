# payments.py
from __future__ import annotations
import os
import logging
from typing import Optional, Tuple, Dict, Any

from web3 import Web3
import httpx

LOG = logging.getLogger("payments")

# ---- ENV ----
WALLET_ADDRESS = (os.getenv("WALLET_ADDRESS") or "").strip()
MIN_CONFIRMATIONS = int(os.getenv("MIN_CONFIRMATIONS", "1"))  # aumente em produção
DEBUG_PAYMENTS = os.getenv("DEBUG_PAYMENTS", "0") == "1"
ALLOW_ANY_TO = os.getenv("ALLOW_ANY_TO", "0") == "1"

if not WALLET_ADDRESS:
    LOG.warning("WALLET_ADDRESS não definido!")

# ---- CHAINS SUPORTADAS (adicione mais se quiser) ----
CHAINS: Dict[str, Dict[str, str]] = {
    "0x38":   {"name": "BNB Smart Chain", "rpc": os.getenv("RPC_0X38", "https://bsc-dataseed.binance.org"), "sym": "BNB",  "cg_native": "binancecoin", "cg_platform": "binance-smart-chain"},
    "0x1":    {"name": "Ethereum",        "rpc": os.getenv("RPC_0X1", "https://rpc.ankr.com/eth"),          "sym": "ETH",  "cg_native": "ethereum",    "cg_platform": "ethereum"},
    "0x89":   {"name": "Polygon",         "rpc": os.getenv("RPC_0X89", "https://polygon-rpc.com"),           "sym": "MATIC","cg_native": "polygon-pos", "cg_platform": "polygon-pos"},
    "0xa4b1": {"name": "Arbitrum One",    "rpc": os.getenv("RPC_0XA4B1", "https://arb1.arbitrum.io/rpc"),    "sym": "ETH",  "cg_native": "ethereum",    "cg_platform": "arbitrum-one"},
    "0xa":    {"name": "OP Mainnet",      "rpc": os.getenv("RPC_0XA", "https://mainnet.optimism.io"),        "sym": "ETH",  "cg_native": "ethereum",    "cg_platform": "optimistic-ethereum"},
    "0x2105": {"name": "Base",            "rpc": os.getenv("RPC_0X2105", "https://mainnet.base.org"),        "sym": "ETH",  "cg_native": "ethereum",    "cg_platform": "base"},
    "0xa86a": {"name": "Avalanche",       "rpc": os.getenv("RPC_0XA86A", "https://api.avax.network/ext/bc/C/rpc"),"sym":"AVAX","cg_native":"avalanche-2","cg_platform":"avalanche"},
    "0xfa":   {"name": "Fantom",          "rpc": os.getenv("RPC_0XFA", "https://rpc.ftm.tools"),             "sym": "FTM",  "cg_native": "fantom",      "cg_platform": "fantom"},
    "0xe708": {"name": "Linea",           "rpc": os.getenv("RPC_0XE708", "https://rpc.linea.build"),         "sym": "ETH",  "cg_native": "ethereum",    "cg_platform": "linea"},
    "0x144":  {"name": "zkSync Era",      "rpc": os.getenv("RPC_0X144", "https://mainnet.era.zksync.io"),    "sym": "ETH",  "cg_native": "ethereum",    "cg_platform": "zksync"},
}

# ---- Constantes & helpers ----
ERC20_TRANSFER_SIG = Web3.keccak(text="Transfer(address,address,uint256)").hex()  # evento
ERC20_TRANSFER_SELECTOR = "0xa9059cbb"  # função transfer(address,uint256)

def _w3(rpc: str) -> Web3:
    return Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 15}))

def _topic_addr(topic_hex: str) -> str:
    # pega os últimos 20 bytes
    return Web3.to_checksum_address("0x" + topic_hex[-40:])

async def _get_confirmations(w3: Web3, block_number: Optional[int]) -> int:
    if block_number is None:
        return 0
    latest = w3.eth.block_number
    return max(0, latest - block_number)

async def _cg_get(url: str) -> Optional[dict]:
    try:
        async with httpx.AsyncClient(timeout=12) as cli:
            r = await cli.get(url, headers={"accept": "application/json"})
            r.raise_for_status()
            return r.json()
    except Exception as e:
        if DEBUG_PAYMENTS:
            LOG.warning("Coingecko GET falhou: %s", e)
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

    if DEBUG_PAYMENTS:
        LOG.info("[resolve] chain=%s to_tx=%s value=%s", chain_id, tx.get("to"), tx.get("value"))

    receipt = None
    if tx.get("blockHash"):
        try:
            receipt = w3.eth.get_transaction_receipt(tx_hash)
        except Exception as e:
            if DEBUG_PAYMENTS:
                LOG.warning("get_transaction_receipt falhou: %s", e)

    confirmations = await _get_confirmations(w3, tx.get("blockNumber"))
    if confirmations < MIN_CONFIRMATIONS:
        return False, f"Aguardando confirmações: {confirmations}/{MIN_CONFIRMATIONS}", None, {"confirmations": confirmations}

    if receipt and receipt.get("status") != 1:
        return False, "Transação revertida.", None, {"confirmations": confirmations}

    details: Dict[str, Any] = {"chain_id": chain_id, "confirmations": confirmations}
    to_addr = (tx.get("to") or "").lower()

    # -------- nativo --------
    if WALLET_ADDRESS and to_addr == WALLET_ADDRESS.lower():
        value_wei = int(tx["value"])
        amount_native = float(value_wei) / float(10 ** 18)
        px = await _usd_native(chain_id, amount_native)
        if not px:
            return False, "Preço USD indisponível (nativo).", None, details
        price_usd, paid_usd = px
        sym = CHAINS[chain_id]["sym"]
        details.update({"type":"native","token_symbol":sym,"amount_human":amount_native,"price_usd":price_usd,"paid_usd":paid_usd})
        return True, f"{sym} nativo OK em {CHAINS[chain_id]['name']}: ${paid_usd:.2f}", paid_usd, details

    # -------- token (ERC-20/BEP-20) por LOG Transfer --------
    if receipt:
        if DEBUG_PAYMENTS:
            LOG.info("[logs] %d logs", len(receipt.get("logs", [])))

        found_value_raw = None
        found_token_addr = None

        for i, log in enumerate(receipt.get("logs", [])):
            try:
                addr = (log.get("address") or "").lower()
                topics = log.get("topics") or []
                if len(topics) < 3:
                    continue
                t0 = topics[0].hex().lower() if hasattr(topics[0],'hex') else str(topics[0]).lower()
                if t0 != ERC20_TRANSFER_SIG.lower():
                    continue
                t2 = topics[2].hex() if hasattr(topics[2],'hex') else str(topics[2])
                toA = _topic_addr(t2)

                if DEBUG_PAYMENTS:
                    val_dbg = log.get("data") or "0x0"
                    LOG.info("[DEBUG TRANSFER] log#%s token=%s to=%s value=%s", i, addr, toA, val_dbg)

                if WALLET_ADDRESS and toA.lower() != WALLET_ADDRESS.lower():
                    continue

                data_hex = log.get("data") or "0x0"
                value_raw = int(data_hex, 16)

                found_value_raw = value_raw
                found_token_addr = Web3.to_checksum_address(addr)
                break
            except Exception as e:
                if DEBUG_PAYMENTS:
                    LOG.warning("[logs] erro ao ler log #%s: %s", i, e)

        if found_value_raw is not None and found_token_addr:
            decimals = _erc20_decimals(w3, found_token_addr)
            symbol = _erc20_symbol(w3, found_token_addr) or "TOKEN"
            px = await _usd_token(chain_id, found_token_addr, found_value_raw, decimals)
            if not px:
                return False, "Preço USD indisponível (token).", None, details
            price_usd, paid_usd = px
            amount_human = float(found_value_raw) / float(10 ** decimals)
            details.update({
                "type":"erc20","token_address":found_token_addr,"token_symbol":symbol,
                "amount_human":amount_human,"price_usd":price_usd,"paid_usd":paid_usd
            })
            return True, f"Token {symbol} OK em {CHAINS[chain_id]['name']}: ${paid_usd:.2f}", paid_usd, details

    # -------- FALLBACK: decodificar calldata transfer(address,uint256) --------
    try:
        data: str = tx.get("input") or tx.get("data") or ""
        if isinstance(data, bytes):
            data = data.hex()
        if isinstance(data, str) and data.startswith("0x") and len(data) >= 10:
            selector = data[:10].lower()
            if selector == ERC20_TRANSFER_SELECTOR and len(data) >= 10 + 64 + 64:
                raw_to = data[10:10+64]
                raw_val = data[10+64:10+64+64]
                to_from_input = Web3.to_checksum_address("0x" + raw_to[-40:])
                value_raw = int(raw_val, 16)

                if DEBUG_PAYMENTS:
                    LOG.info("[fallback input] to=%s value_raw=%s", to_from_input, value_raw)

                if WALLET_ADDRESS and to_from_input.lower() == WALLET_ADDRESS.lower():
                    token_addr = Web3.to_checksum_address(tx["to"])
                    decimals = _erc20_decimals(w3, token_addr)
                    symbol   = _erc20_symbol(w3, token_addr) or "TOKEN"
                    px = await _usd_token(chain_id, token_addr, value_raw, decimals)
                    if not px:
                        return False, "Preço USD indisponível (token).", None, details
                    price_usd, paid_usd = px
                    amount_human = float(value_raw) / float(10 ** decimals)
                    details.update({
                        "type":"erc20","token_address":token_addr,"token_symbol":symbol,
                        "amount_human":amount_human,"price_usd":price_usd,"paid_usd":paid_usd,
                        "fallback":"input"
                    })
                    return True, f"Token {symbol} OK em {CHAINS[chain_id]['name']}: ${paid_usd:.2f}", paid_usd, details
    except Exception as e:
        if DEBUG_PAYMENTS:
            LOG.warning("[fallback input] erro ao decodificar: %s", e)

    # Se chegou aqui, não achou destino para nossa carteira
    if ALLOW_ANY_TO:
        # modo teste: aceita mesmo que o destino não bata
        if receipt:
            # tenta estimar valor total do primeiro Transfer
            for log in receipt.get("logs", []):
                topics = log.get("topics") or []
                if len(topics) >= 3:
                    t0 = topics[0].hex().lower() if hasattr(topics[0],'hex') else str(topics[0]).lower()
                    if t0 == ERC20_TRANSFER_SIG.lower():
                        data_hex = log.get("data") or "0x0"
                        value_raw = int(data_hex, 16)
                        token_addr = Web3.to_checksum_address((log.get("address") or "0x0"))
                        decimals = _erc20_decimals(w3, token_addr)
                        px = await _usd_token(chain_id, token_addr, value_raw, decimals)
                        paid_usd = px[1] if px else 0.0
                        return True, f"[TESTE] Transfer aceito (ANY_TO) em {CHAINS[chain_id]['name']}: ${paid_usd:.2f}", paid_usd, {"any_to": True}
        return True, f"[TESTE] Nativo aceito (ANY_TO) em {CHAINS[chain_id]['name']}", 0.0, {"any_to": True}

    # debug de porque não bateu
    dbg = ""
    if DEBUG_PAYMENTS:
        dbg = (
            f"\nchain: {CHAINS[chain_id]['name']} ({chain_id})"
            f"\nwallet: {WALLET_ADDRESS}"
            f"\ntx.to (contrato ou destino nativo): {tx.get('to')}"
            f"\nlogs: {len(receipt.get('logs', [])) if receipt else 0}"
        )
    return False, "Nenhuma transferência válida p/ a carteira destino." + dbg, None, details


async def resolve_payment_usd_autochain(tx_hash: str) -> Tuple[bool, str, Optional[float], Dict[str, Any]]:
    """
    Percorre as chains configuradas tentando localizar e valorar a transação.
    Retorna: (ok, msg, amount_usd, details)
    """
    for chain_id, meta in CHAINS.items():
        w3 = _w3(meta["rpc"])
        try:
            _ = w3.eth.get_transaction(tx_hash)
        except Exception:
            continue  # não existe nessa chain
        ok, msg, usd, details = await _resolve_on_chain(w3, chain_id, tx_hash)
        if DEBUG_PAYMENTS:
            LOG.info("[result %s] ok=%s msg=%s usd=%s details=%s", meta["name"], ok, msg, usd, details)
        return ok, msg, usd, details
    return False, "Transação não encontrada nas chains suportadas.", None, {}

# Opcional: usado pelo backend/webapp
def get_wallet_address() -> str:
    return WALLET_ADDRESS
