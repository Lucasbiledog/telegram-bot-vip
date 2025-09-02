from __future__ import annotations

import os
import logging
from typing import Optional, Tuple, Dict, Any

import httpx
from web3 import Web3

LOG = logging.getLogger("payments")

# ====== ENV ======
WALLET_ADDRESS = (os.getenv("WALLET_ADDRESS") or "").strip()
if not WALLET_ADDRESS:
    raise RuntimeError("WALLET_ADDRESS não configurada no ambiente.")

MIN_CONFIRMATIONS = int(os.getenv("MIN_CONFIRMATIONS", "1"))  # em produção use 3~12
DEBUG_PAYMENTS = os.getenv("DEBUG_PAYMENTS", "0") == "1"
ALLOW_ANY_TO = os.getenv("ALLOW_ANY_TO", "0") == "1"  # aceita destino diferente (apenas para testes)
WEB3AUTH_CLIENT_ID = os.getenv("WEB3AUTH_CLIENT_ID", "").strip()

# ====== helpers de RPC (preferir Web3Auth/Infura quando disponível) ======
def _wa_rpc(chain_hex: str, fallback: str) -> str:
    """
    Se WEB3AUTH_CLIENT_ID existir, usa o endpoint do Web3Auth:
    https://api.web3auth.io/infura-service/v1/<chainHex>/<CLIENT_ID>
    Caso contrário, usa fallback público.
    """
    if WEB3AUTH_CLIENT_ID:
        return f"https://api.web3auth.io/infura-service/v1/{chain_hex}/{WEB3AUTH_CLIENT_ID}"
    return fallback

# ====== tabela de chains suportadas ======
#  chain_hex -> { rpc, sym, cg_native, cg_platform }
CHAINS: Dict[str, Dict[str, str]] = {
    # EVM majors
    "0x1":    {"rpc": _wa_rpc("0x1",    "https://rpc.ankr.com/eth"),               "sym": "ETH",  "cg_native": "ethereum",      "cg_platform": "ethereum"},
    "0x38":   {"rpc": _wa_rpc("0x38",   "https://bsc-dataseed.binance.org"),       "sym": "BNB",  "cg_native": "binancecoin",   "cg_platform": "binance-smart-chain"},
    "0x89":   {"rpc": _wa_rpc("0x89",   "https://polygon-rpc.com"),                "sym": "MATIC","cg_native": "polygon-pos",   "cg_platform": "polygon-pos"},
    "0xa4b1": {"rpc": _wa_rpc("0xa4b1", "https://arb1.arbitrum.io/rpc"),           "sym": "ETH",  "cg_native": "ethereum",      "cg_platform": "arbitrum-one"},
    "0xa":    {"rpc": _wa_rpc("0xa",    "https://mainnet.optimism.io"),            "sym": "ETH",  "cg_native": "ethereum",      "cg_platform": "optimistic-ethereum"},
    "0x2105": {"rpc": _wa_rpc("0x2105", "https://mainnet.base.org"),               "sym": "ETH",  "cg_native": "ethereum",      "cg_platform": "base"},
    "0xa86a": {"rpc": _wa_rpc("0xa86a", "https://api.avax.network/ext/bc/C/rpc"),  "sym": "AVAX", "cg_native": "avalanche-2",   "cg_platform": "avalanche"},
    "0xfa":   {"rpc": _wa_rpc("0xfa",   "https://rpc.ftm.tools"),                  "sym": "FTM",  "cg_native": "fantom",        "cg_platform": "fantom"},
    "0xe708": {"rpc": _wa_rpc("0xe708", "https://rpc.linea.build"),                "sym": "ETH",  "cg_native": "ethereum",      "cg_platform": "linea"},
    "0x144":  {"rpc": _wa_rpc("0x144",  "https://mainnet.era.zksync.io"),          "sym": "ETH",  "cg_native": "ethereum",      "cg_platform": "zksync"},

    # você pode seguir adicionando mais da sua lista; o algoritmo abaixo funciona igual
}

# ====== overrides de tokens populares (quando o CoinGecko não resolve por contrato) ======
TOKEN_OVERRIDES: Dict[str, Dict[str, str]] = {
    # BSC (BEP20)
    "0x7130d2a12b9bcBFAe4f2634d864A1Ee1Ce3Ead9c".lower(): {"symbol": "BTCB", "cg_id": "bitcoin"},
    "0x55d398326f99059fF775485246999027B3197955".lower(): {"symbol": "USDT", "cg_id": "tether"},
    "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d".lower(): {"symbol": "USDC", "cg_id": "usd-coin"},
    "0xe9e7cea3dedca5984780bafc599bd69add087d56".lower(): {"symbol": "BUSD", "cg_id": "binance-usd"},
    # Adicione aqui outros contratos que você quer aceitar com mapeamento fixo…
}

ERC20_TRANSFER_SIG = Web3.keccak(text="Transfer(address,address,uint256)").hex().lower()


# ====== web3 utils ======
def _w3(rpc: str) -> Web3:
    return Web3(Web3.HTTPProvider(rpc))

def _topic_addr(topic_hex_or_bytes) -> str:
    if hasattr(topic_hex_or_bytes, "hex"):
        h = topic_hex_or_bytes.hex()
    else:
        h = str(topic_hex_or_bytes)
    return Web3.to_checksum_address("0x" + h[-40:])

# ====== preços ======
async def _cg_get(url: str) -> Optional[dict]:
    try:
        async with httpx.AsyncClient(timeout=12) as cli:
            r = await cli.get(url, headers={"Accept": "application/json"})
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
    token_lc = token_addr.lower()

    # 1) tenta override estático
    if token_lc in TOKEN_OVERRIDES:
        cg_id = TOKEN_OVERRIDES[token_lc]["cg_id"]
        data = await _cg_get(f"https://api.coingecko.com/api/v3/simple/price?ids={cg_id}&vs_currencies=usd")
        if not data or cg_id not in data or "usd" not in data[cg_id]:
            return None
        px = float(data[cg_id]["usd"])
        amt = float(amount_raw) / float(10 ** decimals)
        return px, amt * px

    # 2) tenta plataforma do CoinGecko por contrato
    platform = CHAINS[chain_id]["cg_platform"]
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

# ====== ERC20 helpers ======
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
    # tenta override primeiro
    if token.lower() in TOKEN_OVERRIDES:
        return TOKEN_OVERRIDES[token.lower()]["symbol"]

    raw = _erc20_static_call(w3, token, "0x95d89b41")  # symbol()
    if not raw:
        return "TOKEN"
    try:
        # string dinâmica ABI
        if len(raw) >= 96 and raw[:4] == b"\x00\x00\x00\x20":
            strlen = int.from_bytes(raw[64:96], "big")
            return raw[96:96 + strlen].decode("utf-8", errors="ignore") or "TOKEN"
        # string fixa (bytes32)
        return raw.rstrip(b"\x00").decode("utf-8", errors="ignore") or "TOKEN"
    except Exception:
        return "TOKEN"

async def _get_confirmations(w3: Web3, block_number: Optional[int]) -> int:
    if block_number is None:
        return 0
    latest = w3.eth.block_number
    return max(0, latest - block_number)

# ====== núcleo: resolver a tx numa chain específica ======
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
        details.update({"type": "native", "token_symbol": sym, "amount_human": amount_native, "price_usd": price_usd, "paid_usd": paid_usd})
        return True, f"{sym} nativo OK em {chain_id}: ${paid_usd:.2f}", paid_usd, details

    # ERC-20 (Transfer p/ nossa carteira)
    if not receipt:
        return False, "Receipt indisponível para token.", None, details

    found_value_raw = None
    found_token_addr = None
    erc20_to = None

    for log in receipt.get("logs", []):
        addr = (log.get("address") or "").lower()
        topics = log.get("topics") or []
        if len(topics) < 3:
            continue
        t0 = topics[0].hex().lower() if hasattr(topics[0], "hex") else str(topics[0]).lower()
        if t0 != ERC20_TRANSFER_SIG:
            continue
        # topics[2] = to
        to_topic = topics[2]
        try:
            erc20_to = _topic_addr(to_topic)
        except Exception:
            continue

        if WALLET_ADDRESS and erc20_to.lower() != WALLET_ADDRESS.lower():
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
        if ALLOW_ANY_TO:
            # Em modo teste, aceita mesmo que não seja para a carteira destino
            for log in receipt.get("logs", []):
                addr = (log.get("address") or "").lower()
                topics = log.get("topics") or []
                if len(topics) < 3:
                    continue
                t0 = topics[0].hex().lower() if hasattr(topics[0], "hex") else str(topics[0]).lower()
                if t0 != ERC20_TRANSFER_SIG:
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
            return False, "Nenhuma transferência válida p/ a carteira destino.", None, details

    decimals = _erc20_decimals(w3, found_token_addr)
    symbol = _erc20_symbol(w3, found_token_addr) or "TOKEN"
    px = await _usd_token(chain_id, found_token_addr, found_value_raw, decimals)
    if not px:
        return False, "Preço USD indisponível (token).", None, details
    price_usd, paid_usd = px
    amount_human = float(found_value_raw) / float(10 ** decimals)

    details.update({
        "type": "erc20",
        "token_address": found_token_addr,
        "token_symbol": symbol,
        "amount_human": amount_human,
        "price_usd": price_usd,
        "paid_usd": paid_usd,
        "erc20_to": erc20_to,
    })
    return True, f"Token {symbol} OK em {chain_id}: ${paid_usd:.2f}", paid_usd, details


# ====== API pública usada pelo bot/página ======
async def resolve_payment_usd_autochain(tx_hash: str) -> Tuple[bool, str, Optional[float], Dict[str, Any]]:
    """
    Percorre as chains cadastradas e tenta resolver a transação.
    Retorna: (ok, mensagem, valor_em_usd, detalhes)
    """
    if not tx_hash or not tx_hash.startswith("0x") or len(tx_hash) < 20:
        return False, "Hash inválido.", None, {}

    for chain_id, meta in CHAINS.items():
        w3 = _w3(meta["rpc"])
        try:
            _ = w3.eth.get_transaction(tx_hash)
        except Exception:
            continue  # não existe nessa chain, tenta próxima

        ok, msg, usd, details = await _resolve_on_chain(w3, chain_id, tx_hash)

        if DEBUG_PAYMENTS:
            # anexa informações úteis no modo debug
            dbg = {
                "chain": chain_id,
                "rpc": meta["rpc"],
                "wallet_expected": WALLET_ADDRESS,
                "details": details,
            }
            LOG.info("[DEBUG resolve] %s | %s", msg, dbg)

        return ok, msg, usd, details

    return False, "Transação não encontrada nas chains suportadas.", None, {}
