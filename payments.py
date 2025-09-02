from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import suppress
from typing import Any, Dict, Optional, Tuple

import httpx
from web3 import Web3

LOG = logging.getLogger("payments")

# =========================
# Configuração via ENV
# =========================
WALLET_ADDRESS = (os.getenv("WALLET_ADDRESS") or "").strip()
if WALLET_ADDRESS and not WALLET_ADDRESS.startswith("0x"):
    # evita confusão de formato
    raise RuntimeError("WALLET_ADDRESS inválido. Use endereço 0x...")

MIN_CONFIRMATIONS = int(os.getenv("MIN_CONFIRMATIONS", "1"))  # aumente em produção
DEBUG_PAYMENTS = os.getenv("DEBUG_PAYMENTS", "0") == "1"
ALLOW_ANY_TO = os.getenv("ALLOW_ANY_TO", "0") == "1"  # aceita destino diferente (somente testes)

COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY", "").strip()
PRICE_TTL_SECONDS = int(os.getenv("PRICE_TTL_SECONDS", "600"))  # 10min
PRICE_MAX_RETRIES = int(os.getenv("PRICE_MAX_RETRIES", "3"))
PRICE_RETRY_BASE_DELAY = float(os.getenv("PRICE_RETRY_BASE_DELAY", "0.6"))

# Cache simples em memória: key -> (price, ts)
_PRICE_CACHE: Dict[str, Tuple[float, float]] = {}


def _price_cache_get(key: str) -> Optional[float]:
    item = _PRICE_CACHE.get(key)
    if not item:
        return None
    price, ts = item
    if time.time() - ts <= PRICE_TTL_SECONDS:
        return price
    return None


def _price_cache_put(key: str, price: float) -> None:
    _PRICE_CACHE[key] = (price, time.time())


# =========================
# Chains suportadas
# =========================
# Adicione mais entradas conforme precisar.
CHAINS: Dict[str, Dict[str, str]] = {
    # Ethereum / EVMs
    "0x1": {"rpc": "https://rpc.ankr.com/eth", "sym": "ETH", "cg_native": "ethereum", "cg_platform": "ethereum"},
    "0x38": {"rpc": "https://bsc-dataseed.binance.org", "sym": "BNB", "cg_native": "binancecoin", "cg_platform": "binance-smart-chain"},
    "0x89": {"rpc": "https://polygon-rpc.com", "sym": "MATIC", "cg_native": "polygon-pos", "cg_platform": "polygon-pos"},
    "0xa4b1": {"rpc": "https://arb1.arbitrum.io/rpc", "sym": "ETH", "cg_native": "ethereum", "cg_platform": "arbitrum-one"},
    "0xa": {"rpc": "https://mainnet.optimism.io", "sym": "ETH", "cg_native": "ethereum", "cg_platform": "optimistic-ethereum"},
    "0x2105": {"rpc": "https://mainnet.base.org", "sym": "ETH", "cg_native": "ethereum", "cg_platform": "base"},
    "0xa86a": {"rpc": "https://api.avax.network/ext/bc/C/rpc", "sym": "AVAX", "cg_native": "avalanche-2", "cg_platform": "avalanche"},
    "0x144": {"rpc": "https://mainnet.era.zksync.io", "sym": "ETH", "cg_native": "ethereum", "cg_platform": "zksync"},
    "0xe708": {"rpc": "https://rpc.linea.build", "sym": "ETH", "cg_native": "ethereum", "cg_platform": "linea"},
}

# =========================
# Mapeamentos úteis
# =========================

# Signature do evento Transfer(address,address,uint256)
ERC20_TRANSFER_SIG = Web3.keccak(text="Transfer(address,address,uint256)").hex().lower()

# Alguns tokens “wrapped/mirrors” mapeados para ids nativos no CoinGecko
# BTCB (BSC) -> bitcoin
KNOWN_TOKEN_TO_CGID = {
    # chainId:tokenAddress -> cg_id
    f"0x38:{'0x7130d2a12b9bcbfae4f2634d864a1ee1ce3ead9c'}": "bitcoin",
}


# =========================
# Utilitários Web3
# =========================
def _w3(rpc: str) -> Web3:
    return Web3(Web3.HTTPProvider(rpc))


def _topic_addr(topic_hex: str) -> str:
    """Extrai endereço dos últimos 20 bytes de um topic32."""
    if topic_hex.startswith("0x"):
        topic_hex = topic_hex[2:]
    return Web3.to_checksum_address("0x" + topic_hex[-40:])


async def _get_confirmations(w3: Web3, block_number: Optional[int]) -> int:
    if block_number is None:
        return 0
    latest = w3.eth.block_number
    return max(0, latest - block_number)


# =========================
# CoinGecko (com retry/backoff + cache)
# =========================
async def _cg_get(url: str) -> Optional[dict]:
    headers = {}
    if COINGECKO_API_KEY:
        headers["x-cg-pro-api-key"] = COINGECKO_API_KEY

    delay = PRICE_RETRY_BASE_DELAY
    last_err = None
    for attempt in range(1, PRICE_MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=12) as cli:
                r = await cli.get(url, headers=headers)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                LOG.warning("Coingecko 429 (rate-limit). attempt=%d url=%s", attempt, url)
                await asyncio.sleep(delay)
                delay *= 2
                continue
            last_err = f"{r.status_code} {r.text[:140]}"
            await asyncio.sleep(delay)
            delay *= 1.5
        except Exception as e:
            last_err = str(e)
            await asyncio.sleep(delay)
            delay *= 1.5

    LOG.warning("Coingecko GET falhou após retries: %s", last_err)
    return None


async def _usd_native(chain_id: str, amount_native: float) -> Optional[Tuple[float, float]]:
    cg_id = CHAINS[chain_id]["cg_native"]
    cache_key = f"native:{cg_id}"
    cached = _price_cache_get(cache_key)
    if cached is not None:
        px = float(cached)
        return px, amount_native * px

    data = await _cg_get(f"https://api.coingecko.com/api/v3/simple/price?ids={cg_id}&vs_currencies=usd")
    if not data or cg_id not in data or "usd" not in data[cg_id]:
        stale = _PRICE_CACHE.get(cache_key)
        if stale:
            px = float(stale[0])
            LOG.info("[price-fallback] usando cache expirado p/ %s: %f", cache_key, px)
            return px, amount_native * px
        return None

    px = float(data[cg_id]["usd"])
    _price_cache_put(cache_key, px)
    return px, amount_native * px


async def _usd_token(chain_id: str, token_addr: str, amount_raw: int, decimals: int) -> Optional[Tuple[float, float]]:
    token_addr_lc = token_addr.lower()
    amount = float(amount_raw) / float(10 ** decimals)

    # 1) tenta mapeamento “nativo” (ex.: BTCB -> bitcoin)
    alt_cgid = KNOWN_TOKEN_TO_CGID.get(f"{chain_id}:{token_addr_lc}")
    if alt_cgid:
        cache_key = f"native:{alt_cgid}"
        cached = _price_cache_get(cache_key)
        if cached is not None:
            px = float(cached)
            return px, amount * px
        data = await _cg_get(f"https://api.coingecko.com/api/v3/simple/price?ids={alt_cgid}&vs_currencies=usd")
        if data and alt_cgid in data and "usd" in data[alt_cgid]:
            px = float(data[alt_cgid]["usd"])
            _price_cache_put(cache_key, px)
            return px, amount * px
        LOG.info("[price] falhou alt_cgid=%s p/ token %s; tentando plataforma CG...", alt_cgid, token_addr_lc)

    # 2) fluxo padrão por plataforma/contrato
    platform = CHAINS[chain_id]["cg_platform"]
    cache_key = f"token:{platform}:{token_addr_lc}"
    cached = _price_cache_get(cache_key)
    if cached is not None:
        px = float(cached)
        return px, amount * px

    data = await _cg_get(
        f"https://api.coingecko.com/api/v3/simple/token_price/{platform}"
        f"?contract_addresses={token_addr_lc}&vs_currencies=usd"
    )
    if data:
        for k, v in data.items():
            if k.lower() == token_addr_lc and "usd" in v:
                px = float(v["usd"])
                _price_cache_put(cache_key, px)
                return px, amount * px

    # 3) fallback com cache expirado, se existir
    stale = _PRICE_CACHE.get(cache_key)
    if stale:
        px = float(stale[0])
        LOG.info("[price-fallback] usando cache expirado p/ %s: %f", cache_key, px)
        return px, amount * px

    return None


# =========================
# ERC-20 helpers
# =========================
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
        # string dinâmica (ABI)
        if len(raw) >= 96 and raw[:4] == b"\x00\x00\x00\x20":
            strlen = int.from_bytes(raw[64:96], "big")
            return raw[96:96 + strlen].decode("utf-8", errors="ignore") or "TOKEN"
        # string padded
        return raw.rstrip(b"\x00").decode("utf-8", errors="ignore") or "TOKEN"
    except Exception:
        return "TOKEN"


def _parse_log_value_data(data_field: Any) -> Optional[int]:
    """
    data_field pode vir como str "0x..." OU bytes.
    Retorna int do valor (uint256) ou None.
    """
    try:
        if isinstance(data_field, (bytes, bytearray)):
            # bytes ABI: 32 bytes, mas alguns nós retornam tamanho exato do inteiro
            return int.from_bytes(data_field, "big")
        if isinstance(data_field, str):
            if data_field.startswith("0x") or data_field.startswith("0X"):
                return int(data_field, 16)
            # string sem 0x? tenta como decimal
            return int(data_field, 10)
    except Exception as e:
        LOG.warning("[logs] falha parse value data: %s", e)
    return None


# =========================
# Resolver pagamento
# =========================
async def _resolve_on_chain(w3: Web3, chain_id: str, tx_hash: str) -> Tuple[bool, str, Optional[float], Dict[str, Any]]:
    # 1) get_transaction
    try:
        tx = w3.eth.get_transaction(tx_hash)
    except Exception:
        return False, "Transação não encontrada.", None, {}

    # 2) confirmações e status
    receipt = None
    if tx.get("blockHash"):
        with suppress(Exception):
            receipt = w3.eth.get_transaction_receipt(tx_hash)

    confirmations = await _get_confirmations(w3, tx.get("blockNumber"))
    if confirmations < MIN_CONFIRMATIONS:
        return False, f"Aguardando confirmações: {confirmations}/{MIN_CONFIRMATIONS}", None, {"confirmations": confirmations}

    if receipt and receipt.get("status") != 1:
        return False, "Transação revertida.", None, {"confirmations": confirmations}

    details: Dict[str, Any] = {"chain_id": chain_id, "confirmations": confirmations}

    # 3) Nativo?
    tx_to = (tx.get("to") or "").lower()
    LOG.info("[resolve] chain=%s to_tx=%s value=%s", chain_id, tx_to, int(tx.get("value", 0)))

    if WALLET_ADDRESS and tx_to == WALLET_ADDRESS.lower() and int(tx.get("value", 0)) > 0:
        value_wei = int(tx["value"])
        amount_native = float(value_wei) / float(10 ** 18)
        px = await _usd_native(chain_id, amount_native)
        if not px:
            return False, "Preço USD indisponível (nativo).", None, details
        price_usd, paid_usd = px
        sym = CHAINS[chain_id]["sym"]
        details.update({"type": "native", "token_symbol": sym, "amount_human": amount_native, "price_usd": price_usd, "paid_usd": paid_usd})
        return True, f"{sym} nativo OK em {human_chain(chain_id)}: ${paid_usd:.2f}", paid_usd, details

    # 4) ERC-20 por logs (Transfer)
    if receipt:
        logs = receipt.get("logs", [])
        LOG.info("[logs] %d logs", len(logs))
        for idx, log in enumerate(logs):
            try:
                addr = (log.get("address") or "").lower()
                topics = log.get("topics") or []
                if len(topics) < 3:
                    continue
                t0 = topics[0].hex().lower() if hasattr(topics[0], "hex") else str(topics[0]).lower()
                if t0 != ERC20_TRANSFER_SIG:
                    continue
                t2 = topics[2].hex() if hasattr(topics[2], "hex") else str(topics[2])
                toA = _topic_addr(t2)
                if WALLET_ADDRESS and toA.lower() != WALLET_ADDRESS.lower():
                    continue

                value_raw = _parse_log_value_data(log.get("data"))
                if value_raw is None or value_raw <= 0:
                    continue

                token_addr = Web3.to_checksum_address(addr)
                decimals = _erc20_decimals(w3, token_addr)
                symbol = _erc20_symbol(w3, token_addr) or "TOKEN"

                px = await _usd_token(chain_id, token_addr, value_raw, decimals)
                if not px:
                    return False, "Preço USD indisponível (token).", None, details
                price_usd, paid_usd = px
                amount_human = float(value_raw) / float(10 ** decimals)

                details.update({
                    "type": "erc20",
                    "token_address": token_addr,
                    "token_symbol": symbol,
                    "amount_human": amount_human,
                    "price_usd": price_usd,
                    "paid_usd": paid_usd,
                })
                return True, f"Token {symbol} OK em {human_chain(chain_id)}: ${paid_usd:.2f}", paid_usd, details
            except Exception as e:
                LOG.warning("[logs] erro ao ler log #%d: %s", idx, e)

    # 5) Fallback: input data (transfer(to,value))
    # Se a transação chamou o contrato do token diretamente.
    try:
        inp: str = tx.get("input") or ""
        if inp and inp.startswith("0xa9059cbb") and WALLET_ADDRESS:
            # 4 bytes sig + 32 bytes to + 32 bytes value
            if len(inp) >= 10 + 64 + 64:
                to_hex = inp[10 + (64 - 40):10 + 64]  # últimos 20 bytes do 1º arg
                toA = Web3.to_checksum_address("0x" + to_hex[-40:])
                value_hex = inp[10 + 64:10 + 64 + 64]
                value_raw = int(value_hex, 16)

                if toA.lower() == WALLET_ADDRESS.lower() and value_raw > 0:
                    token_addr = Web3.to_checksum_address(tx_to) if tx_to else None
                    if token_addr:
                        decimals = _erc20_decimals(w3, token_addr)
                        symbol = _erc20_symbol(w3, token_addr) or "TOKEN"
                        px = await _usd_token(chain_id, token_addr, value_raw, decimals)
                        if not px:
                            return False, "Preço USD indisponível (token).", None, details
                        price_usd, paid_usd = px
                        amount_human = float(value_raw) / float(10 ** decimals)
                        details.update({
                            "type": "erc20",
                            "token_address": token_addr,
                            "token_symbol": symbol,
                            "amount_human": amount_human,
                            "price_usd": price_usd,
                            "paid_usd": paid_usd,
                        })
                        return True, f"Token {symbol} OK em {human_chain(chain_id)}: ${paid_usd:.2f}", paid_usd, details
    except Exception as e:
        LOG.warning("[fallback input] erro parse input: %s", e)

    # 6) Caso destino não combine
    reason = (
        "Destino não confere para esta transação (nativo)" if int(tx.get("value", 0)) > 0
        else "Nenhuma transferência válida p/ a carteira destino."
    )

    if ALLOW_ANY_TO:
        return False, f"{reason} (ALLOW_ANY_TO está ativo).", None, details

    if DEBUG_PAYMENTS:
        # ajuda a debugar
        dbg = {
            "to_tx": tx_to,
            "wallet": WALLET_ADDRESS,
            "logs": len(receipt.get("logs", [])) if receipt else 0,
        }
        return False, f"[DEBUG] {reason}\n{dbg}", None, details

    return False, reason, None, details


def human_chain(chain_id: str) -> str:
    # nomes mais amigáveis em alguns casos
    if chain_id == "0x38":
        return "BNB Smart Chain"
    return chain_id


async def resolve_payment_usd_autochain(tx_hash: str) -> Tuple[bool, str, Optional[float], Dict[str, Any]]:
    """
    Percorre as chains configuradas. Ao achar a tx em alguma delas,
    resolve nela e retorna (ok, mensagem, usd, detalhes).
    """
    for chain_id, meta in CHAINS.items():
        w3 = _w3(meta["rpc"])
        with suppress(Exception):
            tx = w3.eth.get_transaction(tx_hash)
            if tx:
                ok, msg, usd, details = await _resolve_on_chain(w3, chain_id, tx_hash)
                LOG.info("[result %s] ok=%s msg=%s usd=%s details=%s", human_chain(chain_id), ok, msg, usd, details)
                return ok, msg, usd, details
    return False, "Transação não encontrada nas chains suportadas.", None, {}


# =========================
# Helpers para o main.py
# =========================
def get_wallet_address() -> str:
    return WALLET_ADDRESS or ""


