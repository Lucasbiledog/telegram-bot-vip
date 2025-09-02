from __future__ import annotations
import os, logging, time
from typing import Optional, Tuple, Dict, Any
from web3 import Web3
import httpx

LOG = logging.getLogger("payments")

# ========= ENV / FLAGS =========
DEBUG_PAYMENTS = os.getenv("DEBUG_PAYMENTS", "0") == "1"
ALLOW_ANY_TO = os.getenv("ALLOW_ANY_TO", "0") == "1"
WALLET_ADDRESS = (os.getenv("WALLET_ADDRESS") or "").strip()
MIN_CONFIRMATIONS = int(os.getenv("MIN_CONFIRMATIONS", "1"))
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY", "").strip()  # opcional

# ========= Chains =========
CHAINS: Dict[str, Dict[str, str]] = {
    "0x38": {"rpc":"https://bsc-dataseed.binance.org","sym":"BNB","cg_native":"binancecoin","cg_platform":"binance-smart-chain"},
    "0x1": {"rpc":"https://rpc.ankr.com/eth","sym":"ETH","cg_native":"ethereum","cg_platform":"ethereum"},
    "0x89":{"rpc":"https://polygon-rpc.com","sym":"MATIC","cg_native":"polygon-pos","cg_platform":"polygon-pos"},
    "0xa4b1":{"rpc":"https://arb1.arbitrum.io/rpc","sym":"ETH","cg_native":"ethereum","cg_platform":"arbitrum-one"},
    "0xa":{"rpc":"https://mainnet.optimism.io","sym":"ETH","cg_native":"ethereum","cg_platform":"optimistic-ethereum"},
    "0x2105":{"rpc":"https://mainnet.base.org","sym":"ETH","cg_native":"ethereum","cg_platform":"base"},
    "0xa86a":{"rpc":"https://api.avax.network/ext/bc/C/rpc","sym":"AVAX","cg_native":"avalanche-2","cg_platform":"avalanche"},
    "0xfa":{"rpc":"https://rpc.ftm.tools","sym":"FTM","cg_native":"fantom","cg_platform":"fantom"},
    "0xe708":{"rpc":"https://rpc.linea.build","sym":"ETH","cg_native":"ethereum","cg_platform":"linea"},
    "0x144":{"rpc":"https://mainnet.era.zksync.io","sym":"ETH","cg_native":"ethereum","cg_platform":"zksync"},
    # ... adicione outras conforme precise
}

# ========= Tratamento especial de tokens “wrapped/pegged” =========
# BTCB (BSC) usa o preço do bitcoin.
SPECIAL_TOKEN_TO_CGID = {
    "0x7130d2a12b9bcbfae4f2634d864a1ee1ce3ead9c": "bitcoin",
}

# ========= Cache simples de preços =========
_PRICE_CACHE: Dict[str, Tuple[float, float]] = {}  # key -> (ts, price)

def _price_get_cached(key: str, ttl_sec: int = 300) -> Optional[float]:
    tup = _PRICE_CACHE.get(key)
    if not tup: return None
    ts, px = tup
    if time.time() - ts <= ttl_sec:
        return px
    _PRICE_CACHE.pop(key, None)
    return None

def _price_set_cached(key: str, px: float) -> None:
    _PRICE_CACHE[key] = (time.time(), px)

# ========= Web3 helpers =========
ERC20_TRANSFER_SIG = Web3.keccak(text="Transfer(address,address,uint256)").hex()

def _w3(rpc: str) -> Web3:
    return Web3(Web3.HTTPProvider(rpc))

def _topic_addr(topic_hex: str) -> str:
    return Web3.to_checksum_address("0x" + topic_hex[-40:])

# ========= CoinGecko (com retry/backoff e API key) =========
async def _cg_get(url: str, is_token: bool = False) -> Optional[dict]:
    headers = {}
    if COINGECKO_API_KEY:
        headers["x-cg-pro-api-key"] = COINGECKO_API_KEY
    tries = 3
    backoff = 0.6
    for i in range(tries):
        try:
            timeout = httpx.Timeout(12.0)
            async with httpx.AsyncClient(timeout=timeout) as cli:
                r = await cli.get(url, headers=headers)
                if r.status_code == 429 and i < tries - 1:
                    await asyncio.sleep(backoff)
                    backoff *= 1.6
                    continue
                r.raise_for_status()
                return r.json()
        except Exception as e:
            LOG.warning("Coingecko GET falhou: %s", e)
            if i < tries - 1:
                await asyncio.sleep(backoff)
                backoff *= 1.6
    return None

async def _usd_native(chain_id: str, amount_native: float) -> Optional[Tuple[float, float]]:
    cg_id = CHAINS[chain_id]["cg_native"]
    cache_key = f"cg:native:{cg_id}"
    px = _price_get_cached(cache_key)
    if px is None:
        data = await _cg_get(f"https://api.coingecko.com/api/v3/simple/price?ids={cg_id}&vs_currencies=usd")
        if not data or cg_id not in data or "usd" not in data[cg_id]:
            return None
        px = float(data[cg_id]["usd"])
        _price_set_cached(cache_key, px)
    return px, amount_native * px

async def _usd_token(chain_id: str, token_addr: str, amount_raw: int, decimals: int) -> Optional[Tuple[float, float]]:
    token_lc = token_addr.lower()
    # 1) mapeamento especial?
    cg_override = SPECIAL_TOKEN_TO_CGID.get(token_lc)
    if cg_override:
        cache_key = f"cg:override:{cg_override}"
        px = _price_get_cached(cache_key)
        if px is None:
            data = await _cg_get(f"https://api.coingecko.com/api/v3/simple/price?ids={cg_override}&vs_currencies=usd")
            if not data or cg_override not in data or "usd" not in data[cg_override]:
                return None
            px = float(data[cg_override]["usd"])
            _price_set_cached(cache_key, px)
    else:
        # 2) consulta por plataforma/contrato
        platform = CHAINS[chain_id]["cg_platform"]
        cache_key = f"cg:token:{platform}:{token_lc}"
        px = _price_get_cached(cache_key)
        if px is None:
            data = await _cg_get(
                f"https://api.coingecko.com/api/v3/simple/token_price/{platform}"
                f"?contract_addresses={token_lc}&vs_currencies=usd",
                is_token=True
            )
            if not data:
                return None
            found = None
            for k, v in data.items():
                if k.lower() == token_lc and "usd" in v:
                    found = float(v["usd"]); break
            if found is None:
                return None
            px = found
            _price_set_cached(cache_key, px)

    amt = float(amount_raw) / float(10 ** decimals)
    return px, amt * px

# ========= ERC-20 helpers =========
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

# ========= Resolve =========
async def _get_confirmations(w3: Web3, block_number: Optional[int]) -> int:
    if block_number is None:
        return 0
    latest = w3.eth.block_number
    return max(0, latest - block_number)

async def _resolve_on_chain(w3: Web3, chain_id: str, tx_hash: str) -> Tuple[bool, str, Optional[float], Dict[str, Any]]:
    try:
        tx = w3.eth.get_transaction(tx_hash)
    except Exception:
        return False, "Transação não encontrada.", None, {}

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

    # Nativo
    to_addr = (tx.get("to") or "").lower()
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

    # ERC-20
    if not receipt:
        return False, "Receipt indisponível para token.", None, details

    found_value_raw = None
    found_token_addr = None
    erc20_to = None

    logs = receipt.get("logs", [])
    LOG.info("[logs] %d logs", len(logs))

    for idx, log in enumerate(logs):
        addr = (log.get("address") or "").lower()
        topics = log.get("topics") or []
        if len(topics) < 3:
            continue
        t0 = topics[0].hex().lower() if hasattr(topics[0],'hex') else str(topics[0]).lower()
        if t0 != ERC20_TRANSFER_SIG.lower():
            continue
        t2 = topics[2].hex() if hasattr(topics[2],'hex') else str(topics[2])
        try:
            erc20_to = _topic_addr(t2)
        except Exception:
            continue
        if WALLET_ADDRESS and erc20_to.lower() != WALLET_ADDRESS.lower():
            continue

        # valor: aceita bytes/HexBytes/str
        data_field = log.get("data")
        try:
            if hasattr(data_field, "hex") and not isinstance(data_field, str):
                # HexBytes -> bytes -> int
                value_raw = int.from_bytes(bytes(data_field), "big")
            elif isinstance(data_field, (bytes, bytearray)):
                value_raw = int.from_bytes(data_field, "big")
            else:
                # string "0x..."
                value_raw = int(str(data_field), 16)
            found_value_raw = value_raw
            found_token_addr = Web3.to_checksum_address(addr)
        except Exception as e:
            LOG.warning("[logs] erro ao ler log #%d: %s", idx, e)
            continue
        break

    # Fallback: alguns nodes retornam value também em input (já tratou acima no seu log)
    if found_value_raw is None and WALLET_ADDRESS and erc20_to and erc20_to.lower() == WALLET_ADDRESS.lower():
        try:
            # último suspiro: usa o campo data bruto
            raw = log.get("data")
            if hasattr(raw, "hex") and not isinstance(raw, str):
                found_value_raw = int.from_bytes(bytes(raw), "big")
            elif isinstance(raw, (bytes, bytearray)):
                found_value_raw = int.from_bytes(raw, "big")
            else:
                found_value_raw = int(str(raw), 16)
            found_token_addr = Web3.to_checksum_address(addr)
            LOG.info("[fallback input] to=%s value_raw=%s", erc20_to, found_value_raw)
        except Exception:
            pass

    if found_value_raw is None or not found_token_addr:
        if DEBUG_PAYMENTS:
            msg = (f"[DEBUG] Nenhuma transferência válida p/ carteira destino.\n"
                   f"to_tx: {to_addr}\nwallet: {WALLET_ADDRESS}\nlogs: {len(logs)}")
            return False, msg, None, details
        return False, "Nenhuma transferência válida p/ a carteira destino.", None, details

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
    return True, f"Token {symbol} OK em {CHAINS[chain_id]['sym']} Chain: ${paid_usd:.2f}", paid_usd, details

async def resolve_payment_usd_autochain(tx_hash: str) -> Tuple[bool, str, Optional[float], Dict[str, Any]]:
    for chain_id, meta in CHAINS.items():
        w3 = _w3(meta["rpc"])
        try:
            w3.eth.get_transaction(tx_hash)
        except Exception:
            continue
        ok, msg, usd, details = await _resolve_on_chain(w3, chain_id, tx_hash)
        LOG.info("[result %s] ok=%s msg=%s usd=%s details=%s", meta.get("sym","?"), ok, msg, usd, details)
        return ok, msg, usd, details
    return False, "Transação não encontrada nas chains suportadas.", None, {}

def get_wallet_address() -> str:
    return WALLET_ADDRESS
