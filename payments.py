# payments.py
from __future__ import annotations

import os
import logging
from typing import Optional, Tuple, Dict, Any, List

from web3 import Web3
import httpx

LOG = logging.getLogger("payments")

# ---------- Flags/Env ----------
DEBUG_PAYMENTS = os.getenv("DEBUG_PAYMENTS", "0") == "1"
MIN_CONFIRMATIONS = int(os.getenv("MIN_CONFIRMATIONS", "1"))  # 1 p/ testes
# carteira destino central
def WALLET() -> str:
    # lê dinamicamente para evitar problemas de ordem de import
    w = (os.getenv("WALLET_ADDRESS") or "").strip()
    return Web3.to_checksum_address(w) if Web3.is_address(w) else w

# ---------- Chains ----------
# chainId(hex) -> meta
CHAINS: Dict[str, Dict[str, str]] = {
    "0x1":    {"name":"Ethereum",          "rpc":"https://rpc.ankr.com/eth",                      "sym":"ETH",  "cg_native":"ethereum",      "cg_platform":"ethereum"},
    "0x38":   {"name":"BNB Smart Chain",   "rpc":"https://bsc-dataseed.binance.org",              "sym":"BNB",  "cg_native":"binancecoin",   "cg_platform":"binance-smart-chain"},
    "0x89":   {"name":"Polygon PoS",       "rpc":"https://polygon-rpc.com",                       "sym":"MATIC","cg_native":"polygon-pos",   "cg_platform":"polygon-pos"},
    "0xa4b1": {"name":"Arbitrum One",      "rpc":"https://arb1.arbitrum.io/rpc",                  "sym":"ETH",  "cg_native":"ethereum",      "cg_platform":"arbitrum-one"},
    "0xa":    {"name":"OP Mainnet",        "rpc":"https://mainnet.optimism.io",                   "sym":"ETH",  "cg_native":"ethereum",      "cg_platform":"optimistic-ethereum"},
    "0x2105": {"name":"Base",              "rpc":"https://mainnet.base.org",                      "sym":"ETH",  "cg_native":"ethereum",      "cg_platform":"base"},
    "0xa86a": {"name":"Avalanche C",       "rpc":"https://api.avax.network/ext/bc/C/rpc",         "sym":"AVAX", "cg_native":"avalanche-2",   "cg_platform":"avalanche"},
    "0xfa":   {"name":"Fantom",            "rpc":"https://rpc.ftm.tools",                         "sym":"FTM",  "cg_native":"fantom",        "cg_platform":"fantom"},
    "0xe708": {"name":"Linea",             "rpc":"https://rpc.linea.build",                       "sym":"ETH",  "cg_native":"ethereum",      "cg_platform":"linea"},
    "0x144":  {"name":"zkSync Era",        "rpc":"https://mainnet.era.zksync.io",                 "sym":"ETH",  "cg_native":"ethereum",      "cg_platform":"zksync"},
}

# Se quiser acrescentar o resto da sua lista depois, adicione entradas aqui
# (mantendo os campos rpc, sym, cg_native, cg_platform).

# ---------- Constantes ----------
# Keccak("Transfer(address,address,uint256)")
ERC20_TRANSFER_SIG = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

# ---------- Utils ----------
def _w3(rpc: str) -> Web3:
    return Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 20}))

def _topic_addr(topic_hex: str) -> str:
    # tópico é 32 bytes; endereço vem nos 20 bytes finais
    return Web3.to_checksum_address("0x" + topic_hex[-40:])

async def _get_confirmations(w3: Web3, block_number: Optional[int]) -> int:
    if block_number is None:
        return 0
    latest = w3.eth.block_number
    return max(0, latest - int(block_number))

async def _cg_get(url: str) -> Optional[dict]:
    try:
        async with httpx.AsyncClient(timeout=12) as cli:
            r = await cli.get(url)
            r.raise_for_status()
            return r.json()
    except Exception as e:
        LOG.warning("Coingecko falhou: %s", e)
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

# ---------- Core resolver com debug ----------
async def _resolve_on_chain(
    w3: Web3, chain_id: str, tx_hash: str, debug_lines: List[str]
) -> Tuple[bool, str, Optional[float], Dict[str, Any]]:
    chain_name = CHAINS[chain_id]["name"]
    debug_lines.append(f"• Cadeia: {chain_name} ({chain_id}) – RPC {CHAINS[chain_id]['rpc']}")
    debug_lines.append(f"• WALLET esperada: {WALLET()}")

    # Buscar tx
    try:
        tx = w3.eth.get_transaction(tx_hash)
    except Exception as e:
        debug_lines.append(f"  - get_transaction falhou/nao encontrou: {e}")
        return False, "Transação não encontrada.", None, {}

    debug_lines.append(f"  - tx.to: {tx.get('to')}  tx.from: {tx.get('from')}  valorWei: {tx.get('value')}")
    receipt = None
    if tx.get("blockHash"):
        try:
            receipt = w3.eth.get_transaction_receipt(tx_hash)
            debug_lines.append(f"  - receipt ok. status={receipt.get('status')} logs={len(receipt.get('logs') or [])}")
        except Exception as e:
            debug_lines.append(f"  - receipt erro: {e}")

    confirmations = await _get_confirmations(w3, tx.get("blockNumber"))
    debug_lines.append(f"  - confirmacoes: {confirmations}/{MIN_CONFIRMATIONS}")
    if confirmations < MIN_CONFIRMATIONS:
        return False, f"Aguardando confirmações: {confirmations}/{MIN_CONFIRMATIONS}", None, {"confirmations": confirmations}

    if receipt and receipt.get("status") != 1:
        return False, "Transação revertida.", None, {"confirmations": confirmations}

    details: Dict[str, Any] = {"chain_id": chain_id, "chain_name": chain_name, "confirmations": confirmations}

    # 1) Nativo (tx.to == WALLET)
    try:
        to_addr = Web3.to_checksum_address(tx.get("to")) if tx.get("to") else None
    except Exception:
        to_addr = None

    if WALLET() and to_addr and to_addr == WALLET():
        value_wei = int(tx["value"])
        amount_native = float(value_wei) / float(10 ** 18)
        debug_lines.append(f"  - nativo para a WALLET. amount={amount_native}")
        px = await _usd_native(chain_id, amount_native)
        if not px:
            debug_lines.append("  - preco USD (nativo) indisponivel")
            return False, "Preço USD indisponível (nativo).", None, details
        price_usd, paid_usd = px
        sym = CHAINS[chain_id]["sym"]
        details.update({"type":"native","token_symbol":sym,"amount_human":amount_native,"price_usd":price_usd,"paid_usd":paid_usd})
        return True, f"{sym} nativo OK em {chain_name}: ${paid_usd:.2f}", paid_usd, details
    else:
        debug_lines.append(f"  - nativo NAO bateu: to={to_addr} vs WALLET={WALLET()}")

    # 2) ERC-20: varrer todos os eventos Transfer do recibo e mostrar por que não casou
    if not receipt:
        debug_lines.append("  - sem recibo; nao consigo validar ERC-20")
        return False, "Receipt indisponível para token.", None, details

    candidate_lines: List[str] = []
    matched_value_raw: Optional[int] = None
    matched_token_addr: Optional[str] = None

    for idx, log in enumerate(receipt.get("logs", [])):
        try:
            addr = Web3.to_checksum_address(log.get("address"))
        except Exception:
            addr = (log.get("address") or "").lower()

        topics = log.get("topics") or []
        if len(topics) < 3:
            candidate_lines.append(f"    • log#{idx} {addr}: sem 3 tópicos")
            continue

        t0 = topics[0].hex().lower() if hasattr(topics[0], 'hex') else str(topics[0]).lower()
        if t0 != ERC20_TRANSFER_SIG:
            candidate_lines.append(f"    • log#{idx} {addr}: nao é Transfer (t0={t0[:10]}...)")
            continue

        t1 = topics[1].hex() if hasattr(topics[1], 'hex') else str(topics[1])
        t2 = topics[2].hex() if hasattr(topics[2], 'hex') else str(topics[2])

        try:
            toA = _topic_addr(t2)
            fromA = _topic_addr(t1)
        except Exception:
            candidate_lines.append(f"    • log#{idx} {addr}: erro ao decodificar enderecos")
            continue

        data_hex = log.get("data") or "0x0"
        try:
            value_raw = int(data_hex, 16)
        except Exception:
            value_raw = 0

        hit = (WALLET() and toA == WALLET())
        candidate_lines.append(
            f"    • log#{idx} token={addr} from={fromA} to={toA} valueRaw={value_raw} "
            + ("<-- MATCH WALLET" if hit else "")
        )

        if hit and matched_value_raw is None:
            matched_value_raw = value_raw
            matched_token_addr = addr

    debug_lines.extend(candidate_lines if candidate_lines else ["    • recibo sem logs"])

    if matched_value_raw is None or not matched_token_addr:
        return False, "Nenhuma transferência válida p/ a carteira destino.", None, details

    # Temos token transferido para a WALLET
    decimals = _erc20_decimals(w3, matched_token_addr)
    symbol = _erc20_symbol(w3, matched_token_addr) or "TOKEN"
    px = await _usd_token(chain_id, matched_token_addr, matched_value_raw, decimals)
    if not px:
        debug_lines.append("  - preco USD (token) indisponivel")
        return False, "Preço USD indisponível (token).", None, details
    price_usd, paid_usd = px
    amount_human = float(matched_value_raw) / float(10 ** decimals)

    details.update({
        "type":"erc20","token_address":matched_token_addr,"token_symbol":symbol,
        "amount_human":amount_human,"price_usd":price_usd,"paid_usd":paid_usd
    })
    return True, f"Token {symbol} OK em {chain_name}: ${paid_usd:.2f}", paid_usd, details


async def resolve_payment_usd_autochain(tx_hash: str) -> Tuple[bool, str, Optional[float], Dict[str, Any]]:
    """
    Percorre as chains configuradas até encontrar a tx.
    Retorna (ok, msg, amount_usd, details) — sempre inclui `details['debug']` quando DEBUG_PAYMENTS=1.
    """
    debug_lines: List[str] = []
    debug_lines.append(f"== DEBUG pagamentos ==")
    debug_lines.append(f"hash: {tx_hash}")
    debug_lines.append(f"WALLET(env): {WALLET()}")
    for chain_id, meta in CHAINS.items():
        w3 = _w3(meta["rpc"])
        # chamada barata: se não existir nessa chain, Web3 lança.
        try:
            _ = w3.eth.get_transaction(tx_hash)
        except Exception:
            continue  # não está nesta chain
        # encontrou — resolve nela
        ok, msg, usd, details = await _resolve_on_chain(w3, chain_id, tx_hash, debug_lines)
        details = details or {}
        if DEBUG_PAYMENTS:
            details["debug"] = debug_lines
        return ok, msg, usd, details

    # não achou em nenhuma
    if DEBUG_PAYMENTS:
        debug_lines.append("hash nao encontrado em nenhuma chain configurada.")
        return False, "Transação não encontrada nas chains suportadas.", None, {"debug": debug_lines}
    return False, "Transação não encontrada nas chains suportadas.", None, {}
