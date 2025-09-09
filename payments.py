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
PRICE_TTL_SECONDS = int(os.getenv("PRICE_TTL_SECONDS", "1800"))  # 30min (aumentado de 10min)
PRICE_MAX_RETRIES = int(os.getenv("PRICE_MAX_RETRIES", "2"))    # Reduzido de 3 para 2
PRICE_RETRY_BASE_DELAY = float(os.getenv("PRICE_RETRY_BASE_DELAY", "2.0"))  # Aumentado de 0.6 para 2.0

# Cache simples em memória: key -> (price, ts)
_PRICE_CACHE: Dict[str, Tuple[float, float]] = {}

# Preços estáticos como fallback quando CoinGecko está indisponível (atualize manualmente)
FALLBACK_PRICES = {
    # Nativos
    "ethereum": 2500.0,
    "binancecoin": 300.0,
    "polygon-pos": 0.9,
    "avalanche-2": 25.0,
    "bitcoin": 43000.0,
    
    # Tokens populares por endereço (chain:address -> preço)
    "0x38:0x7130d2a12b9bcbfae4f2634d864a1ee1ce3ead9c": 43000.0,  # BTCB na BSC
    "0x1:0xa0b86991c31cc170c8b9e71b51e1a53af4e9b8c9e": 1.0,     # USDC na Ethereum
    "0x38:0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d": 1.0,     # USDC na BSC
}


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

# Alguns tokens "wrapped/mirrors" mapeados para ids nativos no CoinGecko
# BTCB (BSC) -> bitcoin
KNOWN_TOKEN_TO_CGID = {
    # chainId:tokenAddress -> cg_id
    f"0x38:{'0x7130d2a12b9bcbfae4f2634d864a1ee1ce3ead9c'}": "bitcoin",
}

# Mapeamento de endereços para símbolos conhecidos (fallback)
KNOWN_TOKEN_SYMBOLS = {
    "0x7130d2a12b9bcbfae4f2634d864a1ee1ce3ead9c": "BTCB",  # BTCB na BSC
    "0xa0b86991c31cc170c8b9e71b51e1a53af4e9b8c9e": "USDC",  # USDC na Ethereum
    "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d": "USDC",   # USDC na BSC
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
    
    # Adicionar delay inicial para evitar rate limiting
    if not COINGECKO_API_KEY:  # Apenas para free tier
        await asyncio.sleep(0.5)  # 500ms delay inicial
    
    for attempt in range(1, PRICE_MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=12) as cli:
                r = await cli.get(url, headers=headers)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                LOG.warning("Coingecko 429 (rate-limit). attempt=%d url=%s", attempt, url)
                await asyncio.sleep(delay)
                delay *= 3  # Aumenta mais agressivamente o delay
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
        # Tentar cache expirado primeiro
        stale = _PRICE_CACHE.get(cache_key)
        if stale:
            px = float(stale[0])
            LOG.info("[price-fallback] usando cache expirado p/ %s: %f", cache_key, px)
            return px, amount_native * px
        
        # Usar preço estático apenas se COINGECKO_API_KEY não estiver configurado
        if not COINGECKO_API_KEY or COINGECKO_API_KEY == "CG-DEMO-API-KEY":
            fallback_price = FALLBACK_PRICES.get(cg_id)
            if fallback_price:
                px = float(fallback_price)
                LOG.warning("[price-static-fallback] CoinGecko indisponível, usando preço estático p/ %s: %f", cg_id, px)
                _price_cache_put(cache_key, px)  # Cache o preço estático
                return px, amount_native * px
        
        LOG.error("[price-fail] Falha ao obter preço para %s - configure COINGECKO_API_KEY", cg_id)
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
        
        # Fallback estático apenas sem API key
        if not COINGECKO_API_KEY or COINGECKO_API_KEY == "CG-DEMO-API-KEY":
            fallback_price = FALLBACK_PRICES.get(alt_cgid)
            if fallback_price:
                px = float(fallback_price)
                LOG.warning("[price-static-fallback] usando preço estático p/ alt_cgid %s: %f", alt_cgid, px)
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

    # 4) Fallback estático apenas sem API key
    if not COINGECKO_API_KEY or COINGECKO_API_KEY == "CG-DEMO-API-KEY":
        token_key = f"{chain_id}:{token_addr_lc}"
        fallback_price = FALLBACK_PRICES.get(token_key)
        if fallback_price:
            px = float(fallback_price)
            LOG.warning("[price-static-fallback] usando preço estático p/ token %s: %f", token_key, px)
            _price_cache_put(cache_key, px)
            return px, amount * px

    LOG.error("[price-fail] Falha ao obter preço para token %s:%s - configure COINGECKO_API_KEY", chain_id, token_addr_lc)
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
    # Primeiro, tentar mapeamento conhecido
    known_symbol = KNOWN_TOKEN_SYMBOLS.get(token.lower())
    if known_symbol:
        LOG.info(f"Usando símbolo conhecido para {token}: {known_symbol}")
        return known_symbol
        
    raw = _erc20_static_call(w3, token, "0x95d89b41")  # symbol()
    if not raw:
        return "TOKEN"
    try:
        # string dinâmica (ABI) - formato: offset(32) + length(32) + data
        if len(raw) >= 96 and raw[:4] == b"\x00\x00\x00\x20":
            strlen = int.from_bytes(raw[64:96], "big")
            if strlen > 0 and strlen <= 32:  # Validar tamanho
                symbol_bytes = raw[96:96 + strlen]
                symbol = symbol_bytes.decode("utf-8", errors="ignore").strip()
                return symbol or "TOKEN"
        
        # string padded (formato antigo) - dados diretos nos 32 bytes
        elif len(raw) >= 32:
            # Remover bytes nulos e decodificar
            symbol_bytes = raw.rstrip(b"\x00")
            if symbol_bytes:
                symbol = symbol_bytes.decode("utf-8", errors="ignore").strip()
                # Filtrar apenas caracteres alfanuméricos
                symbol = ''.join(c for c in symbol if c.isalnum())
                return symbol or "TOKEN"
        
        return "TOKEN"
    except Exception as e:
        LOG.warning(f"Erro ao decodificar símbolo do token {token}: {e}")
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
# Database Models - importa do main.py
# =========================

# =========================
# Telegram Command Handlers
# =========================
from telegram import Update
from telegram.ext import ContextTypes
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from telegram import Bot

import re

TX_RE = re.compile(r'^(0x)?[0-9a-fA-F]+$')

def normalize_tx_hash(s: str) -> Optional[str]:
    if not s:
        return None
    s = s.strip()
    if not TX_RE.match(s):
        return None
    if s.startswith("0x"):
        # precisa ter 66 chars: 0x + 64 hex
        return s.lower() if len(s) == 66 else None
    else:
        # sem 0x: precisa ter 64 hex; adiciona 0x
        return ("0x" + s.lower()) if len(s) == 64 else None

async def pagar_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /pagar - redireciona para página de checkout"""
    if not WALLET_ADDRESS:
        return await update.effective_message.reply_text("Método de pagamento não configurado. (WALLET_ADDRESS ausente)")

    user = update.effective_user
    chat = update.effective_chat
    msg = update.effective_message

    # Import WEBAPP_URL from config
    try:
        from config import WEBAPP_URL
    except ImportError:
        WEBAPP_URL = None

    # Criar botão WebApp para checkout se disponível
    if WEBAPP_URL:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
        from utils import send_with_retry, reply_with_retry, make_link_sig
        import time
        import os

        # Gerar parâmetros de segurança para o link
        uid = user.id
        ts = int(time.time())
        sig = make_link_sig(os.getenv("BOT_SECRET", "default"), uid, ts)

        # URL com parâmetros de segurança
        secure_url = f"{WEBAPP_URL}?uid={uid}&ts={ts}&sig={sig}"

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "💳 Pagar com Crypto - Checkout",
                web_app=WebAppInfo(url=secure_url)
            )]
        ])

        checkout_msg = (
            f"💸 <b>Pagamento VIP via Cripto</b>\n\n"
            f"✅ Clique no botão abaixo para acessar nossa página de checkout segura\n"
            f"🔒 Pague com qualquer criptomoeda\n"
            f"⚡ Ativação automática após confirmação\n\n"
            f"💰 <b>Planos disponíveis:</b>\n"
            f"• 30 dias: $0.05\n"
            f"• 60 dias: $1.00\n"
            f"• 180 dias: $1.50\n"
            f"• 365 dias: $2.00"
        )

        sent = await send_with_retry(
            context.bot.send_message,
            chat_id=user.id,
            text=checkout_msg,
            parse_mode="HTML",
            reply_markup=keyboard,
        )

        if sent is not None:
            if chat.type != "private":
                await reply_with_retry(
                    msg,
                    "📱 Te enviei o link de pagamento no privado!",
                )
        else:
            await reply_with_retry(
                msg,
                checkout_msg,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
    
    else:
        # Fallback caso não tenha WEBAPP_URL: instruções manuais
        instrucoes = (
            f"💸 <b>Pagamento via Cripto</b>\n"
            f"1) Abra seu banco de cripto.\n"
            f"2) Envie o valor para a carteira:\n<code>{WALLET_ADDRESS}</code>\n"
            f"3) Depois me mande aqui: <code>/tx &lt;hash_da_transacao&gt;</code>\n\n"
            f"⚙️ Valido on-chain (mín. {MIN_CONFIRMATIONS} confirmações).\n"
            f"✅ Aprovando, te envio o convite do VIP no privado."
        )

        try:
            await context.bot.send_message(
                chat_id=user.id,
                text=instrucoes,
                parse_mode="HTML"
            )
            if chat.type != "private":
                await msg.reply_text("📱 Te enviei as instruções no privado!")
        except Exception:
            await msg.reply_text(instrucoes, parse_mode="HTML")

async def tx_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /tx - verificar transação"""
    msg = update.effective_message
    user = update.effective_user
    
    if not context.args:
        return await msg.reply_text("Uso: /tx <hash_da_transacao> (ex.: 0x… com 66 caracteres)")
    
    tx_raw = context.args[0]
    tx_hash = normalize_tx_hash(tx_raw)
    if not tx_hash:
        return await msg.reply_text(
            "Hash inválida. Use formato: 0x... (66 caracteres) ou sem 0x (64 caracteres)."
        )
    
    # Import Payment from main
    try:
        from main import Payment, SessionLocal
    except ImportError:
        return await msg.reply_text("Erro: Banco de dados não configurado.")
    
    # Verificar se já existe
    with SessionLocal() as s:
        existing = s.query(Payment).filter(Payment.tx_hash == tx_hash).first()
        if existing:
            if existing.status == "approved":
                return await msg.reply_text(
                    f"✅ Seu pagamento já estava aprovado!\n"
                    f"Se ainda não recebeu o convite VIP, entre em contato."
                )
            else:
                return await msg.reply_text(
                    f"⏳ Pagamento já registrado e está sendo analisado.\n"
                    f"Status atual: {existing.status}"
                )
    
    # Verificar transação on-chain
    try:
        ok, msg_result, usd_paid, details = await resolve_payment_usd_autochain(tx_hash)
        
        if ok and usd_paid:
            # Import necessário para funções do main
            from utils import choose_plan_from_usd
            
            # Determinar plano baseado no valor real pago (sem preços estáticos)
            plan_days = choose_plan_from_usd(usd_paid)
            
            if plan_days:
                # Registrar pagamento
                with SessionLocal() as s:
                    # Extrair informações do token
                    token_symbol = details.get("token_symbol", "Unknown")
                    token_amount = details.get("amount", "N/A")
                    
                    p = Payment(
                        user_id=user.id,
                        username=user.username,
                        tx_hash=tx_hash,
                        chain=details.get("chain_id", "unknown"),
                        amount=str(token_amount),
                        token_symbol=token_symbol,
                        usd_value=str(usd_paid),
                        vip_days=plan_days,
                        status="approved",
                        created_at=dt.datetime.now()
                    )
                    s.add(p)
                    s.commit()
                
                # Criar/estender VIP
                from utils import vip_upsert_and_get_until
                vip_until = await vip_upsert_and_get_until(user.id, user.username, plan_days)
                
                return await msg.reply_text(
                    f"✅ Pagamento confirmado: ${usd_paid:.2f}\n"
                    f"VIP válido até {vip_until.strftime('%d/%m/%Y')}\n"
                    f"Aguarde o convite do grupo VIP!"
                )
            else:
                return await msg.reply_text(
                    f"❌ Valor pago (${usd_paid:.2f}) insuficiente para qualquer plano VIP."
                )
        else:
            return await msg.reply_text(f"❌ {msg_result}")
            
    except Exception as e:
        LOG.error(f"Erro ao verificar transação {tx_hash}: {e}")
        return await msg.reply_text("❌ Erro interno ao verificar transação.")

async def listar_pendentes_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando admin para listar pagamentos pendentes"""
    try:
        from main import Payment, SessionLocal
    except ImportError:
        return await update.effective_message.reply_text("Erro: Banco de dados não configurado.")
    
    with SessionLocal() as s:
        pend = s.query(Payment).filter(Payment.status == "pending").order_by(Payment.created_at.asc()).all()
        if not pend:
            return await update.effective_message.reply_text("Sem pagamentos pendentes.")
        lines = [
            f"- user_id:{p.user_id} @{p.username or '-'} | {p.tx_hash} | {p.chain} | {p.created_at.strftime('%d/%m %H:%M')}" 
            for p in pend
        ]
        await update.effective_message.reply_text("Pagamentos pendentes:\n" + "\n".join(lines))

# =========================
# Helpers para o main.py
# =========================
def get_wallet_address() -> str:
    return WALLET_ADDRESS or ""

def get_min_confirmations() -> int:
    return MIN_CONFIRMATIONS

def get_supported_chains() -> Dict[str, Dict[str, str]]:
    return CHAINS.copy()

async def aprovar_tx_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando admin para aprovar transação manualmente"""
    from main import is_admin, Payment, SessionLocal
    from utils import vip_upsert_and_get_until, create_one_time_invite
    
    if not (update.effective_user and is_admin(update.effective_user.id)):
        return await update.effective_message.reply_text("Apenas admins.")
    if not context.args:
        return await update.effective_message.reply_text("Uso: /aprovar_tx <hash>")

    tx_hash = normalize_tx_hash(context.args[0])
    if not tx_hash:
        return await update.effective_message.reply_text("Hash inválida.")

    with SessionLocal() as s:
        p = s.query(Payment).filter(Payment.tx_hash == tx_hash).first()
        if not p:
            return await update.effective_message.reply_text("Transação não encontrada.")
        if p.status == "approved":
            return await update.effective_message.reply_text("Já aprovada.")

        try:
            # Extend VIP
            vip_until = await vip_upsert_and_get_until(p.user_id, p.username, p.days)
            p.status = "approved"
            p.vip_until = vip_until
            s.commit()

            valid_str = vip_until.strftime("%d/%m/%Y")

            # Criar convite
            from main import application, GROUP_VIP_ID
            invite_link = await create_one_time_invite(
                application.bot, GROUP_VIP_ID, expire_seconds=7200
            )

            # Notify user
            success_msg = (
                f"✅ **Pagamento aprovado!**\n"
                f"VIP válido até {valid_str}\n\n"
            )
            if invite_link:
                success_msg += f"🔗 [Entrar no grupo VIP]({invite_link})"
            else:
                success_msg += "Entre em contato para receber o convite do grupo VIP."

            try:
                await application.bot.send_message(
                    chat_id=p.user_id, text=success_msg, parse_mode="Markdown"
                )
            except Exception:
                pass

            await update.effective_message.reply_text(
                f"✅ Transação aprovada para user_id:{p.user_id} @{p.username}\n"
                f"VIP válido até {valid_str}"
            )

        except Exception as e:
            s.rollback()
            import logging
            logging.exception("Erro ao aprovar transação")
            await update.effective_message.reply_text(f"❌ Erro: {e}")

async def rejeitar_tx_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando admin para rejeitar transação"""
    from main import is_admin, Payment, SessionLocal
    
    if not (update.effective_user and is_admin(update.effective_user.id)):
        return await update.effective_message.reply_text("Apenas admins.")
    if not context.args:
        return await update.effective_message.reply_text("Uso: /rejeitar_tx <hash>")

    tx_hash = normalize_tx_hash(context.args[0])
    if not tx_hash:
        return await update.effective_message.reply_text("Hash inválida.")

    with SessionLocal() as s:
        p = s.query(Payment).filter(Payment.tx_hash == tx_hash).first()
        if not p:
            return await update.effective_message.reply_text("Transação não encontrada.")
        if p.status == "rejected":
            return await update.effective_message.reply_text("Já rejeitada.")

        p.status = "rejected"
        s.commit()

        # Notificar usuário
        try:
            from main import application
            await application.bot.send_message(
                chat_id=p.user_id,
                text="❌ **Seu pagamento foi rejeitado.**\nEntre em contato se acha que há um erro.",
                parse_mode="Markdown"
            )
        except Exception:
            pass

        await update.effective_message.reply_text(
            f"❌ Transação rejeitada para user_id:{p.user_id} @{p.username}"
        )

# =========================
# Função principal de aprovação
# =========================
async def approve_by_usd_and_invite(tg_id, username: Optional[str], tx_hash: str, notify_user: bool = True):
    """Valida transação e gera convite VIP - aceita UIDs temporários"""
    from main import SessionLocal, Payment, GROUP_VIP_ID, application
    from utils import create_one_time_invite, vip_upsert_and_get_until, choose_plan_from_usd
    import datetime as dt
    
    # Verificar se hash já existe
    with SessionLocal() as s:
        existing = s.query(Payment).filter(Payment.tx_hash == tx_hash).first()
        if existing:
            return False, "Hash já usada", {"error": "hash_used"}

    # Resolver pagamento
    ok, info, usd, details = await resolve_payment_usd_autochain(tx_hash)
    if not ok:
        return False, info, {"details": details}

    # Verificar se valor cobre algum plano baseado no valor real (sem preços estáticos)
    days = choose_plan_from_usd(usd or 0.0)
    if not days:
        return False, f"Valor insuficiente (${usd:.2f})", {"details": details, "usd": usd}

    # Verificar se é UID temporário
    is_temp_uid = isinstance(tg_id, str) and tg_id.startswith("temp_")
    actual_tg_id = None
    until = None
    link = None
    
    if not is_temp_uid:
        try:
            actual_tg_id = int(tg_id)
            # Estender VIP apenas se for ID real
            until = await vip_upsert_and_get_until(actual_tg_id, username, days)
            
            # Gerar convite de 1 uso
            link = await create_one_time_invite(application.bot, GROUP_VIP_ID, expire_seconds=7200, member_limit=1)
            if not link:
                return False, "Falha ao gerar convite", {"error": "invite_failed"}
        except (ValueError, TypeError):
            is_temp_uid = True

    # Salvar pagamento
    with SessionLocal() as s:
        # Extrair informações do payment para salvar
        token_symbol = details.get("token_symbol", "Unknown")
        token_amount = details.get("amount", "N/A")
        
        p = Payment(
            tx_hash=tx_hash,
            user_id=actual_tg_id if actual_tg_id else 0,  # 0 para pagamentos sem ID válido
            username=username,
            chain=details.get("chain_id", "unknown"),
            amount=str(token_amount),
            token_symbol=token_symbol,
            usd_value=str(usd),
            vip_days=days,
            status="approved",
            created_at=dt.datetime.now(dt.timezone.utc)
        )
        s.add(p)
        s.commit()

    if is_temp_uid:
        msg = f"✅ Pagamento confirmado (${usd:.2f})!\nPlano: {days} dias\n\n⚠️ Para receber o convite do grupo VIP, forneça seu ID do Telegram válido."
        return True, msg, {"usd": usd, "days": days, "temp_uid": True}
    else:
        msg = f"✅ Pagamento confirmado (${usd:.2f})!\nPlano: {days} dias\nConvite VIP: {link}"
        
        if notify_user and actual_tg_id:
            try:
                await application.bot.send_message(chat_id=actual_tg_id, text=msg)
            except Exception:
                pass

        return True, msg, {"invite": link, "until": until.isoformat(), "usd": usd, "days": days}

# =========================
# Função para verificar se hash já foi usada
# =========================
async def hash_exists(tx_hash: str) -> bool:
    """Verifica se hash já foi usada"""
    from main import SessionLocal, Payment
    with SessionLocal() as s:
        return bool(s.query(Payment).filter(Payment.tx_hash == tx_hash).first())

# =========================
# Função para salvar hash de pagamento
# =========================
async def store_payment_hash(tx_hash: str, tg_id: int):
    """Salva hash de pagamento no banco"""
    from main import SessionLocal, Payment
    import datetime as dt
    
    with SessionLocal() as s:
        p = Payment(
            tx_hash=tx_hash,
            user_id=tg_id,
            status="approved",
            created_at=dt.datetime.now(dt.timezone.utc)
        )
        s.add(p)
        s.commit()

# =========================
# Função para obter preços do banco
# =========================
async def get_prices_from_db():
    """Obtém preços dos planos do banco de dados"""
    try:
        from main import SessionLocal, Config
        with SessionLocal() as s:
            config = s.query(Config).filter(Config.key == "vip_prices").first()
            if config:
                import json
                return json.loads(config.value)
    except Exception:
        pass
    return DEFAULT_VIP_PRICES_USD

