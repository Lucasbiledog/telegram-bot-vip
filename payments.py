from __future__ import annotations
import os
import logging
from typing import Optional, Tuple, Dict, Any

from web3 import Web3
import httpx

LOG = logging.getLogger("payments")

# ========= Config .env =========
WALLET_ADDRESS = (os.getenv("WALLET_ADDRESS") or "").strip()
if not WALLET_ADDRESS:
    raise RuntimeError("WALLET_ADDRESS não definida no .env")

MIN_CONFIRMATIONS = int(os.getenv("MIN_CONFIRMATIONS", "1"))  # 1 p/ testes
DEBUG_PAYMENTS = os.getenv("DEBUG_PAYMENTS", "0") == "1"
ALLOW_ANY_TO = os.getenv("ALLOW_ANY_TO", "0") == "1"   # aceitar txs cujo 'to' != WALLET_ADDRESS (apenas teste)

# ========= Catálogo de cadeias EVM =========
# Para habilitar, defina RPC_<NOME> no .env com a URL do Web3Auth (api.web3auth.io/infura-service/v1/<chainId>/<TOKEN>)
# O código só ativa a chain se a env correspondente existir.
CATALOG: Dict[str, Dict[str, str]] = {
    # chainId(hex): {env, sym, cg_native, cg_platform}
    "0x1":    {"env": "RPC_ETHEREUM",  "sym": "ETH",   "cg_native": "ethereum",     "cg_platform": "ethereum"},
    "0x38":   {"env": "RPC_BNB",       "sym": "BNB",   "cg_native": "binancecoin",  "cg_platform": "binance-smart-chain"},
    "0x89":   {"env": "RPC_POLYGON",   "sym": "MATIC", "cg_native": "polygon-pos",  "cg_platform": "polygon-pos"},
    "0xa4b1": {"env": "RPC_ARBITRUM",  "sym": "ETH",   "cg_native": "ethereum",     "cg_platform": "arbitrum-one"},
    "0xa":    {"env": "RPC_OPTIMISM",  "sym": "ETH",   "cg_native": "ethereum",     "cg_platform": "optimistic-ethereum"},
    "0x2105": {"env": "RPC_BASE",      "sym": "ETH",   "cg_native": "ethereum",     "cg_platform": "base"},
    "0xa86a": {"env": "RPC_AVALANCHE", "sym": "AVAX",  "cg_native": "avalanche-2",  "cg_platform": "avalanche"},
    "0xfa":   {"env": "RPC_FANTOM",    "sym": "FTM",   "cg_native": "fantom",       "cg_platform": "fantom"},
    "0xe708": {"env": "RPC_LINEA",     "sym": "ETH",   "cg_native": "ethereum",     "cg_platform": "linea"},
    "0x144":  {"env": "RPC_ZKSYNC",    "sym": "ETH",   "cg_native": "ethereum",     "cg_platform": "zksync"},
    "0x1388": {"env": "RPC_MANTLE",    "sym": "MNT",   "cg_native": "mantle",       "cg_platform": "mantle"},
    "0xa4ec": {"env": "RPC_CELO",      "sym": "CELO",  "cg_native": "celo",         "cg_platform": "celo"},
    "0x64":   {"env": "RPC_GNOSIS",    "sym": "xDAI",  "cg_native": "xdai",         "cg_platform": "xdai"},
    "0x19":   {"env": "RPC_CRONOS",    "sym": "CRO",   "cg_native": "cronos",       "cg_platform": "cronos"},
    "0x82750":{"env": "RPC_SCROLL",    "sym": "ETH",   "cg_native": "ethereum",     "cg_platform": "scroll"},
    "0x2a15c308d": {"env": "RPC_PALM", "sym": "PALM",  "cg_native": "palm",         "cg_platform": "palm"},
    "0xcc":   {"env": "RPC_OPBNB",     "sym": "BNB",   "cg_native": "binancecoin",  "cg_platform": "opbnb"},
    "0xa86a2": {"env": "RPC_SONIC",    "sym": "S",     "cg_native": "sonic-2",      "cg_platform": "sonic"},  # opcional (se usar)
    # Adicione aqui mais linhas do seu catálogo conforme habilitar no .env
}

def _enabled_chains_from_env() -> Dict[str, Dict[str, str]]:
    enabled: Dict[str, Dict[str, str]] = {}
    for chain_id, meta in CATALOG.items():
        rpc = os.getenv(meta["env"], "").strip()
        if rpc:
            enabled[chain_id] = {
                "rpc": rpc,
                "sym": meta["sym"],
                "cg_native": meta["cg_native"],
                "cg_platform": meta["cg_platform"],
            }
    if not enabled:
        raise RuntimeError("Nenhuma chain EVM habilitada. Defina RPC_* no .env (ex.: RPC_BNB, RPC_POLYGON, ...)")
    LOG.info("Chains EVM habilitadas: %s", ", ".join(f"{cid}({m['sym']})" for cid, m in enabled.items()))
    return enabled

CHAINS: Dict[str, Dict[str, str]] = _enabled_chains_from_env()

# ========= Helpers Web3 / ERC-20 =========
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

# ========= Preços (CoinGecko) =========
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

# ========= Resolvedor EVM =========
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

    # Nativo (transferência direta para nossa carteira)
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

    # ERC-20: precisa do receipt para ler evento Transfer
    if not receipt:
        return False, "Receipt indisponível para token.", None, details

    found_value_raw = None
    found_token_addr = None
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
        break

    if found_value_raw is None or not found_token_addr:
        reason = "Nenhuma transferência válida p/ a carteira destino."
        if ALLOW_ANY_TO:
            # modo teste: aceita qualquer destino
            reason = None
        if reason:
            if DEBUG_PAYMENTS:
                return False, (
                    "[DEBUG] Sem Transfer para a carteira.\n"
                    f"Esperada: {WALLET_ADDRESS}\n"
                    f"to(tx): {to_addr}\n"
                ), None, details
            return False, "Nenhuma transferência válida p/ a carteira destino.", None, details

    decimals = _erc20_decimals(w3, found_token_addr)
    symbol = _erc20_symbol(w3, found_token_addr) or "TOKEN"
    px = await _usd_token(chain_id, found_token_addr, found_value_raw or 0, decimals)
    if not px:
        return False, "Preço USD indisponível (token).", None, details
    price_usd, paid_usd = px
    amount_human = float((found_value_raw or 0)) / float(10 ** decimals)

    details.update({
        "type":"erc20","token_address":found_token_addr,"token_symbol":symbol,
        "amount_human":amount_human,"price_usd":price_usd,"paid_usd":paid_usd
    })
    return True, f"Token {symbol} OK em {chain_id}: ${paid_usd:.2f}", paid_usd, details

async def resolve_payment_usd_autochain(tx_hash: str) -> Tuple[bool, str, Optional[float], Dict[str, Any]]:
    """
    Tenta localizar a tx percorrendo as chains EVM habilitadas (RPC_* no .env).
    Retorna: (ok, mensagem, amount_usd, detalhes)
    """
    for chain_id, meta in CHAINS.items():
        w3 = _w3(meta["rpc"])
        try:
            # tentativa leve: se não existe nessa chain, levanta/erra e vamos p/ próxima
            _ = w3.eth.get_transaction(tx_hash)
        except Exception:
            continue
        # achou nesta chain
        ok, msg, usd, details = await _resolve_on_chain(w3, chain_id, tx_hash)
        if DEBUG_PAYMENTS:
            LOG.info("resolve[%s]: %s | usd=%s | details=%s", chain_id, msg, usd, details)
        return ok, msg, usd, details
    return False, "Transação não encontrada nas chains suportadas.", None, {}
