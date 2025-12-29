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
PRICE_TTL_SECONDS = int(os.getenv("PRICE_TTL_SECONDS", "14400"))  # 4 horas (14400s) para escalar sem custos - otimizado para 100+ tx/min
PRICE_EXTENDED_TTL_SECONDS = int(os.getenv("PRICE_EXTENDED_TTL_SECONDS", "3600"))  # 1h para casos de rate limit severo
PRICE_MAX_RETRIES = int(os.getenv("PRICE_MAX_RETRIES", "2"))    # Reduzido de 3 para 2
PRICE_RETRY_BASE_DELAY = float(os.getenv("PRICE_RETRY_BASE_DELAY", "5.0"))  # 5s base delay para rate limiting

# Cache simples em memória: key -> (price, ts)
_PRICE_CACHE: Dict[str, Tuple[float, float]] = {}

# Cache de transações validadas: hash -> (timestamp, result_tuple)
# Evita re-validar a mesma transação múltiplas vezes
_TX_VALIDATION_CACHE: Dict[str, Tuple[float, Tuple[bool, str, Optional[float], Dict[str, Any]]]] = {}
TX_VALIDATION_TTL = int(os.getenv("TX_VALIDATION_TTL", "3600"))  # 1 hora de cache para transações validadas

# Preços de fallback: atualizados dinamicamente no startup para tokens principais
FALLBACK_PRICES = {
    # Tokens nativos principais - atualizados automaticamente com preços de mercado atuais (Janeiro 2025)
    "ethereum": 4378.48,
    "binancecoin": 890.57,
    "polygon-pos": 0.27,  # MATIC - Preço atual do mercado
    "avalanche-2": 28.89,
    "fantom": 0.30,
    "crypto-com-chain": 0.26,  # CRO
    "celo": 0.31,
    "moonbeam": 0.07,  # GLMR
    "moonriver": 5.95,  # MOVR
    "mantle": 1.52,  # MNT
    "apecoin": 0.61,  # APE
    "xdai": 1.0,  # xDAI
    
    # Bitcoin e variantes
    "bitcoin": 95000.0,  # Preço aproximado Bitcoin (Dezembro 2025)
    "0x38:0x7130d2a12b9bcbfae4f2634d864a1ee1ce3ead9c": 95000.0,  # BTCB na BSC
    
    # Stablecoins principais (USD = 1.0)
    "0x1:0xa0b86991c31cc170c8b9e71b51e1a53af4e9b8c9e": 1.0,     # USDC na Ethereum
    "0x38:0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d": 1.0,     # USDC na BSC
    "0x89:0x2791bca1f2de4661ed88a30c99a7a9449aa84174": 1.0,     # USDC na Polygon
    "0xa4b1:0xaf88d065e77c8cc2239327c5edb3a432268e5831": 1.0,   # USDC na Arbitrum
    "0xa:0x0b2c639c533813f4aa9d7837caf62653d097ff85": 1.0,      # USDC na Optimism
    "0x2105:0x833589fcd6edb6e08f4c7c32d4f71b54bda02913": 1.0,   # USDC na Base
    
    # USDT variants
    "0x1:0xdac17f958d2ee523a2206206994597c13d831ec7": 1.0,      # USDT na Ethereum
    "0x38:0x55d398326f99059ff775485246999027b3197955": 1.0,     # USDT na BSC
    "0x89:0xc2132d05d31c914a87c6611c10748aeb04b58e8f": 1.0,     # USDT na Polygon
}

# Metadados para auditoria dos preços de fallback
FALLBACK_PRICE_META: Dict[str, Dict[str, Any]] = {
    k: {"source": "manual", "ts": time.time()} for k in FALLBACK_PRICES
}


def _update_fallback_prices() -> None:
    """Atualiza preços de fallback principais a partir do CoinGecko."""
    
    # Lista de tokens para atualizar automaticamente
    price_updates = [
        {
            "cg_id": "bitcoin", 
            "keys": ["bitcoin", "0x38:0x7130d2a12b9bcbfae4f2634d864a1ee1ce3ead9c"],  # BTC/BTCB
            "name": "Bitcoin/BTCB"
        },
        {
            "cg_id": "ethereum",
            "keys": ["ethereum"],
            "name": "Ethereum"
        },
        {
            "cg_id": "binancecoin", 
            "keys": ["binancecoin"],
            "name": "BNB"
        },
        {
            "cg_id": "polygon-pos",
            "keys": ["polygon-pos"],
            "name": "Polygon"
        },
        {
            "cg_id": "avalanche-2",
            "keys": ["avalanche-2"],
            "name": "Avalanche"
        },
        {
            "cg_id": "fantom",
            "keys": ["fantom"],
            "name": "Fantom"
        },
        {
            "cg_id": "crypto-com-chain",
            "keys": ["crypto-com-chain"],
            "name": "Cronos"
        },
        {
            "cg_id": "celo",
            "keys": ["celo"],
            "name": "Celo"
        },
        {
            "cg_id": "moonbeam",
            "keys": ["moonbeam"],
            "name": "Moonbeam"
        },
        {
            "cg_id": "moonriver",
            "keys": ["moonriver"],
            "name": "Moonriver"
        },
        {
            "cg_id": "mantle",
            "keys": ["mantle"],
            "name": "Mantle"
        },
        {
            "cg_id": "apecoin",
            "keys": ["apecoin"],
            "name": "ApeCoin"
        }
    ]
    
    # Construir URL com múltiplas moedas
    coin_ids = [item["cg_id"] for item in price_updates]
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={','.join(coin_ids)}&vs_currencies=usd"
    
    try:
        r = httpx.get(url, timeout=15)
        if r.status_code == 200:
            data = r.json()
            updated_count = 0
            
            for item in price_updates:
                cg_id = item["cg_id"]
                if cg_id in data and "usd" in data[cg_id]:
                    px = float(data[cg_id]["usd"])
                    
                    # Atualizar todas as chaves para este token
                    for key in item["keys"]:
                        old_price = FALLBACK_PRICES.get(key, 0)
                        FALLBACK_PRICES[key] = px
                        FALLBACK_PRICE_META[key] = {"source": "coingecko_auto", "ts": time.time()}
                        updated_count += 1
                        
                        LOG.info(
                            "[AUTO-UPDATE] %s: $%.2f -> $%.2f (key: %s)",
                            item["name"], old_price, px, key
                        )
            
            LOG.info(
                "[AUTO-UPDATE] Atualizados %d preços de fallback em %s",
                updated_count,
                time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
            )
        elif r.status_code == 429:
            LOG.warning("[AUTO-UPDATE] Rate limit (429) - tentando novamente mais tarde")
        else:
            LOG.warning("[AUTO-UPDATE] CoinGecko erro %d", r.status_code)
            
    except Exception as exc:
        LOG.warning("[AUTO-UPDATE] Falha ao atualizar preços de fallback: %s", exc)


# Atualizar preços de fallback no startup
_update_fallback_prices()


# =========================
# Atualização Periódica de Preços de Fallback
# =========================
import threading

def _periodic_price_update():
    """Atualiza preços de fallback a cada 2 horas (reduzido para evitar rate limit)"""
    while True:
        try:
            time.sleep(7200)  # 2 horas (reduzido de 30min para evitar 429)
            LOG.info("[PERIODIC] Iniciando atualização automática de preços...")
            _update_fallback_prices()
        except Exception as e:
            LOG.error("[PERIODIC] Erro na atualização automática: %s", e)

# Iniciar thread em background para atualizações periódicas
_update_thread = threading.Thread(target=_periodic_price_update, daemon=True)
_update_thread.start()
LOG.info("[PERIODIC] Thread de atualização automática de preços iniciada")


def _price_cache_get(key: str, force_refresh: bool = False, allow_extended: bool = False) -> Optional[float]:
    """Obter preço do cache com TTL configurável"""
    if force_refresh and not allow_extended:
        return None
    item = _PRICE_CACHE.get(key)
    if not item:
        return None
    price, ts = item
    age = time.time() - ts
    
    # TTL normal (30 min)
    if age <= PRICE_TTL_SECONDS:
        LOG.info(f"[CACHE-HIT] {key}: ${price:.2f} (idade: {int(age/60)}min)")
        return price
    
    # TTL extendido para casos de rate limit (1 hora)
    if allow_extended and age <= PRICE_EXTENDED_TTL_SECONDS:
        LOG.info(f"[CACHE-EXTENDED] {key}: ${price:.2f} (idade: {int(age/60)}min)")
        return price
    
    return None

def _price_cache_put(key: str, price: float, from_backup: bool = False) -> None:
    """Armazenar preço no cache com timestamp"""
    _PRICE_CACHE[key] = (price, time.time())
    source = "backup-api" if from_backup else "coingecko"
    LOG.info(f"[CACHE-PUT] {key}: ${price:.2f} (fonte: {source})")


# =========================
# Chains suportadas com RPCs de backup
# =========================
CHAINS: Dict[str, Dict[str, Any]] = {
    # Principais EVMs com RPCs de backup (otimizado para 100+ tx/min)
    "0x1": {
        "rpc": "https://rpc.ankr.com/eth",
        "backup_rpcs": [
            "https://eth.llamarpc.com",
            "https://ethereum.publicnode.com",
            "https://eth.drpc.org",
            "https://cloudflare-eth.com",
            "https://rpc.flashbots.net"
        ],
        "sym": "ETH", "cg_native": "ethereum", "cg_platform": "ethereum"
    },
    "0x38": {
        "rpc": "https://bsc-dataseed.binance.org",
        "backup_rpcs": [
            "https://bsc.publicnode.com",
            "https://rpc.ankr.com/bsc",
            "https://bsc.drpc.org",
            "https://bsc-rpc.gateway.pokt.network",
            "https://bscrpc.com"
        ],
        "sym": "BNB", "cg_native": "binancecoin", "cg_platform": "binance-smart-chain"
    },
    "0x89": {
        "rpc": "https://polygon-rpc.com",
        "backup_rpcs": [
            "https://polygon.llamarpc.com",
            "https://rpc.ankr.com/polygon",
            "https://polygon.drpc.org",
            "https://polygon-bor-rpc.publicnode.com",
            "https://polygon.gateway.tenderly.co"
        ],
        "sym": "MATIC", "cg_native": "polygon-pos", "cg_platform": "polygon-pos"
    },
    "0xa4b1": {
        "rpc": "https://arb1.arbitrum.io/rpc",
        "backup_rpcs": [
            "https://arbitrum.llamarpc.com",
            "https://arbitrum.drpc.org",
            "https://rpc.ankr.com/arbitrum",
            "https://arbitrum-one.publicnode.com"
        ],
        "sym": "ETH", "cg_native": "ethereum", "cg_platform": "arbitrum-one"
    },
    "0xa": {
        "rpc": "https://mainnet.optimism.io",
        "backup_rpcs": [
            "https://optimism.llamarpc.com",
            "https://optimism.drpc.org",
            "https://rpc.ankr.com/optimism",
            "https://optimism-rpc.publicnode.com"
        ],
        "sym": "ETH", "cg_native": "ethereum", "cg_platform": "optimistic-ethereum"
    },
    "0x2105": {
        "rpc": "https://mainnet.base.org",
        "backup_rpcs": [
            "https://base.llamarpc.com",
            "https://base.drpc.org",
            "https://rpc.ankr.com/base",
            "https://base-rpc.publicnode.com"
        ],
        "sym": "ETH", "cg_native": "ethereum", "cg_platform": "base"
    },
    "0xa86a": {"rpc": "https://api.avax.network/ext/bc/C/rpc", "sym": "AVAX", "cg_native": "avalanche-2", "cg_platform": "avalanche"},
    "0x144": {"rpc": "https://mainnet.era.zksync.io", "sym": "ETH", "cg_native": "ethereum", "cg_platform": "zksync"},
    "0xe708": {"rpc": "https://rpc.linea.build", "sym": "ETH", "cg_native": "ethereum", "cg_platform": "linea"},
    
    # Expansão completa - Layer 2s e sidechains
    "0x13e31": {"rpc": "https://rpc.blast.io", "sym": "ETH", "cg_native": "ethereum", "cg_platform": "blast"},
    "0xa4ec": {"rpc": "https://forno.celo.org", "sym": "CELO", "cg_native": "celo", "cg_platform": "celo"},
    "0x1388": {"rpc": "https://rpc.mantle.xyz", "sym": "MNT", "cg_native": "mantle", "cg_platform": "mantle"},
    "0xcc": {"rpc": "https://opbnb-mainnet-rpc.bnbchain.org", "sym": "BNB", "cg_native": "binancecoin", "cg_platform": "opbnb"},
    "0x2a15c308d": {"rpc": "https://palm-mainnet.public.blastapi.io", "sym": "PALM", "cg_native": "palm", "cg_platform": "palm"},
    "0x82750": {"rpc": "https://rpc.scroll.io", "sym": "ETH", "cg_native": "ethereum", "cg_platform": "scroll"},
    "0x783": {"rpc": "https://mainnet-swell.alt.technology", "sym": "ETH", "cg_native": "ethereum", "cg_platform": "swellchain"},
    "0x82": {"rpc": "https://unichain-mainnet.alt.technology", "sym": "ETH", "cg_native": "ethereum", "cg_platform": "unichain"},
    
    # Outros EVMs importantes
    "0xfa": {"rpc": "https://rpc.ftm.tools", "sym": "FTM", "cg_native": "fantom", "cg_platform": "fantom"},
    "0x64": {"rpc": "https://rpc.gnosischain.com", "sym": "xDAI", "cg_native": "xdai", "cg_platform": "gnosis"},
    "0x507": {"rpc": "https://mainnet.moonbeam.network", "sym": "GLMR", "cg_native": "moonbeam", "cg_platform": "moonbeam"},
    "0x505": {"rpc": "https://rpc.api.moonriver.moonbeam.network", "sym": "MOVR", "cg_native": "moonriver", "cg_platform": "moonriver"},
    "0x19": {"rpc": "https://evm.cronos.org", "sym": "CRO", "cg_native": "crypto-com-chain", "cg_platform": "cronos"},
    "0x7a69": {"rpc": "https://rpc.zora.energy", "sym": "ETH", "cg_native": "ethereum", "cg_platform": "zora"},
    "0x8453": {"rpc": "https://rpc.zora.energy", "sym": "ETH", "cg_native": "ethereum", "cg_platform": "zora"},
    
    # Redes emergentes e especializadas
    "0x1b3": {"rpc": "https://rpc.apechain.com/http", "sym": "APE", "cg_native": "apecoin", "cg_platform": "apechain"},
    "0x2710": {"rpc": "https://rpc.morphl2.io", "sym": "ETH", "cg_native": "ethereum", "cg_platform": "morph"},
    "0x8274f": {"rpc": "https://rpc.scroll.io", "sym": "ETH", "cg_native": "ethereum", "cg_platform": "scroll"},
    "0xa4b1": {"rpc": "https://nova.arbitrum.io/rpc", "sym": "ETH", "cg_native": "ethereum", "cg_platform": "arbitrum-nova"},
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
    """Cria instância Web3 com timeout configurado"""
    from web3.middleware import geth_poa_middleware
    
    # HTTPProvider com timeout menor para RPCs lentos
    provider = Web3.HTTPProvider(rpc, request_kwargs={'timeout': 12})
    w3 = Web3(provider)
    
    # Middleware para chains PoA (BSC, Polygon, etc)
    if any(chain in rpc.lower() for chain in ['bsc', 'polygon', 'bnb', 'binance']):
        w3.middleware_onion.inject(geth_poa_middleware, layer=0)
    
    return w3

async def _try_get_transaction_with_backup(chain_id: str, tx_hash: str) -> Optional[Any]:
    """Tenta buscar transação no RPC principal e backups"""
    meta = CHAINS[chain_id]
    rpcs_to_try = [meta['rpc']] + meta.get('backup_rpcs', [])
    chain_name = human_chain(chain_id)

    for i, rpc in enumerate(rpcs_to_try):
        try:
            rpc_type = "principal" if i == 0 else f"backup-{i}"
            LOG.info(f"[{chain_name}] Tentando RPC {rpc_type}: {rpc[:50]}...")

            w3 = _w3(rpc)
            tx = await asyncio.wait_for(
                asyncio.to_thread(w3.eth.get_transaction, tx_hash),
                timeout=2.0  # Reduzido para 2s para validação rápida
            )

            if tx and hasattr(tx, 'hash') and tx.hash:
                LOG.info(f"[{chain_name}] ✅ Transação encontrada via RPC {rpc_type}!")
                return tx, w3

        except asyncio.TimeoutError:
            LOG.warning(f"[{chain_name}] Timeout RPC {rpc_type} (>2s)")
        except Exception as e:
            error_str = str(e).lower()
            if "transaction not found" not in error_str and "not found" not in error_str:
                LOG.warning(f"[{chain_name}] Erro RPC {rpc_type}: {str(e)[:80]}")

    return None


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
# CoinGecko + APIs de backup (com retry/backoff + cache)
# =========================

# APIs de backup GRÁTIS para preços de crypto (otimizado para 100+ tx/min)
# CoinGecko → CryptoCompare → CoinCap → Binance → Coinbase
BACKUP_PRICE_APIS = {
    # CryptoCompare - API grátis com 100k requests/mês
    "cryptocompare": {
        "url_template": "https://min-api.cryptocompare.com/data/price?fsym={symbol}&tsyms=USD",
        "symbol_map": {
            "ethereum": "ETH",
            "bitcoin": "BTC",
            "binancecoin": "BNB",
            "polygon-pos": "MATIC",
            "avalanche-2": "AVAX",
            "fantom": "FTM",
            "crypto-com-chain": "CRO",
            "celo": "CELO",
            "moonbeam": "GLMR",
            "moonriver": "MOVR",
            "mantle": "MNT",
            "apecoin": "APE"
        },
        "parser": lambda x: float(x.get("USD", 0)) if x and "USD" in x else None
    },
    # CoinCap - API grátis sem limite de requests
    "coincap": {
        "url_template": "https://api.coincap.io/v2/assets/{coincap_id}",
        "id_map": {
            "ethereum": "ethereum",
            "bitcoin": "bitcoin",
            "binancecoin": "binance-coin",
            "polygon-pos": "polygon",
            "avalanche-2": "avalanche",
            "fantom": "fantom",
            "crypto-com-chain": "crypto-com-coin",
            "celo": "celo",
            "apecoin": "apecoin"
        },
        "parser": lambda x: float(x["data"]["priceUsd"]) if x and "data" in x and "priceUsd" in x["data"] else None
    },
    # Binance - API pública grátis (limitado a pares específicos)
    "binance": {
        "pairs": {
            "ethereum": "ETHUSDT",
            "bitcoin": "BTCUSDT",
            "binancecoin": "BNBUSDT",
            "polygon-pos": "MATICUSDT",
            "avalanche-2": "AVAXUSDT"
        },
        "url_template": "https://api.binance.com/api/v3/ticker/price?symbol={pair}",
        "parser": lambda x: float(x.get("price", 0)) if x and "price" in x else None
    }
}

async def _try_backup_apis(asset: str) -> Optional[float]:
    """Tenta obter preço de APIs de backup GRATUITAS (otimizado para escala)"""

    # Tentar CryptoCompare
    if asset in BACKUP_PRICE_APIS["cryptocompare"]["symbol_map"]:
        symbol = BACKUP_PRICE_APIS["cryptocompare"]["symbol_map"][asset]
        url = BACKUP_PRICE_APIS["cryptocompare"]["url_template"].format(symbol=symbol)
        try:
            LOG.info(f"[BACKUP-API] Tentando CryptoCompare para {asset} ({symbol})...")
            async with httpx.AsyncClient(timeout=8) as cli:
                r = await cli.get(url)
                if r.status_code == 200:
                    data = r.json()
                    price = BACKUP_PRICE_APIS["cryptocompare"]["parser"](data)
                    if price and price > 0:
                        LOG.info(f"[BACKUP-API] ✅ CryptoCompare: {asset} = ${price:.2f}")
                        return price
        except Exception as e:
            LOG.warning(f"[BACKUP-API] Erro CryptoCompare: {str(e)[:80]}")

    # Tentar CoinCap
    if asset in BACKUP_PRICE_APIS["coincap"]["id_map"]:
        coincap_id = BACKUP_PRICE_APIS["coincap"]["id_map"][asset]
        url = BACKUP_PRICE_APIS["coincap"]["url_template"].format(coincap_id=coincap_id)
        try:
            LOG.info(f"[BACKUP-API] Tentando CoinCap para {asset} ({coincap_id})...")
            async with httpx.AsyncClient(timeout=8) as cli:
                r = await cli.get(url)
                if r.status_code == 200:
                    data = r.json()
                    price = BACKUP_PRICE_APIS["coincap"]["parser"](data)
                    if price and price > 0:
                        LOG.info(f"[BACKUP-API] ✅ CoinCap: {asset} = ${price:.2f}")
                        return price
        except Exception as e:
            LOG.warning(f"[BACKUP-API] Erro CoinCap: {str(e)[:80]}")

    # Tentar Binance
    if asset in BACKUP_PRICE_APIS["binance"]["pairs"]:
        pair = BACKUP_PRICE_APIS["binance"]["pairs"][asset]
        url = BACKUP_PRICE_APIS["binance"]["url_template"].format(pair=pair)
        try:
            LOG.info(f"[BACKUP-API] Tentando Binance para {asset} ({pair})...")
            async with httpx.AsyncClient(timeout=8) as cli:
                r = await cli.get(url)
                if r.status_code == 200:
                    data = r.json()
                    price = BACKUP_PRICE_APIS["binance"]["parser"](data)
                    if price and price > 0:
                        LOG.info(f"[BACKUP-API] ✅ Binance: {asset} = ${price:.2f}")
                        return price
        except Exception as e:
            LOG.warning(f"[BACKUP-API] Erro Binance: {str(e)[:80]}")

    LOG.warning(f"[BACKUP-API] Todas as APIs de backup falharam para {asset}")
    return None

async def _cg_get(url: str) -> Optional[dict]:
    headers = {}
    if COINGECKO_API_KEY:
        headers["x-cg-pro-api-key"] = COINGECKO_API_KEY

    delay = PRICE_RETRY_BASE_DELAY
    last_err = None
    
    # Delay inicial inteligente baseado na situação da API
    if not COINGECKO_API_KEY:  # Free tier - muito conservativo
        await asyncio.sleep(10.0)  # 10s inicial
    else:
        await asyncio.sleep(3.0)  # API key ainda conservativo
    
    for attempt in range(1, PRICE_MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=12) as cli:
                r = await cli.get(url, headers=headers)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                LOG.warning("Coingecko 429 (rate-limit). attempt=%d url=%s", attempt, url)
                # Para rate limiting severo, desistir mais rápido e usar fallbacks
                if attempt >= 2:  # Após 2 tentativas, desistir
                    LOG.warning(f"[RATE-LIMIT] Desistindo do CoinGecko após {attempt} tentativas (429), usando fallbacks")
                    break
                
                rate_limit_delay = 20 if COINGECKO_API_KEY else 30  # Delay fixo menor
                LOG.warning(f"[RATE-LIMIT] Rate limited! Aguardando {rate_limit_delay}s (attempt {attempt}/{PRICE_MAX_RETRIES})")
                await asyncio.sleep(rate_limit_delay)
                continue
            last_err = f"{r.status_code} {r.text[:100]}"
            await asyncio.sleep(min(delay, 30))  # Cap de 30s
            delay *= 2
        except Exception as e:
            last_err = str(e)[:100]
            await asyncio.sleep(min(delay, 20))  # Cap menor para outros erros
            delay *= 2

    LOG.warning("Coingecko GET falhou após retries: %s", last_err)
    return None


async def _usd_native(chain_id: str, amount_native: float, force_refresh: bool = False) -> Optional[Tuple[float, float]]:
    cg_id = CHAINS[chain_id]["cg_native"]
    cache_key = f"native:{cg_id}"
    
    # SEMPRE tentar cache primeiro para evitar rate limits
    cached = _price_cache_get(cache_key, force_refresh=False, allow_extended=True)
    if cached is not None:
        px = float(cached)
        usd_value = amount_native * px
        return px, usd_value
    
    # Para BNB, tentar APIs de backup PRIMEIRO (mais rápidas que CoinGecko)
    if cg_id == "binancecoin":
        LOG.info(f"[BACKUP-FIRST] Tentando APIs de backup para {cg_id} antes do CoinGecko...")
        backup_price = await _try_backup_apis(cg_id)
        if backup_price:
            px = backup_price
            usd_value = amount_native * px
            LOG.info(f"[BACKUP-SUCCESS] ✅ {cg_id}: ${px:.2f} | {amount_native} unidades = ${usd_value:.2f}")
            _price_cache_put(cache_key, px, from_backup=True)
            return px, usd_value
    
    LOG.info(f"[LIVE-PRICE] Buscando preço atual da internet para {cg_id}...")
    data = await _cg_get(f"https://api.coingecko.com/api/v3/simple/price?ids={cg_id}&vs_currencies=usd")
    
    # Se CoinGecko falhou, tentar APIs de backup
    if not data or cg_id not in data or "usd" not in data[cg_id]:
        LOG.warning(f"[BACKUP] CoinGecko falhou para {cg_id}, tentando APIs de backup...")
        backup_price = await _try_backup_apis(cg_id)
        if backup_price:
            px = backup_price
            usd_value = amount_native * px
            LOG.info(f"[BACKUP-SUCCESS] ✅ {cg_id}: ${px:.2f} | {amount_native} unidades = ${usd_value:.2f}")
            _price_cache_put(cache_key, px, from_backup=True)  # Cache o preço de backup
            return px, usd_value
    
    if not data or cg_id not in data or "usd" not in data[cg_id]:
        # Tentar cache expirado primeiro
        stale = _PRICE_CACHE.get(cache_key)
        if stale:
            px = float(stale[0])
            LOG.info("[price-fallback] usando cache expirado p/ %s: %f", cache_key, px)
            return px, amount_native * px
        
        # Usar preço de fallback quando API indisponível (rate limiting, etc)
        fallback_price = FALLBACK_PRICES.get(cg_id)
        if fallback_price:
            px = float(fallback_price)
            usd_value = amount_native * px
            meta = FALLBACK_PRICE_META.get(cg_id, {})
            ts = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(meta.get("ts", 0)))
            src = meta.get("source", "manual")
            LOG.warning(
                "[STATIC-FALLBACK] CoinGecko + backup APIs indisponíveis, usando preço estático p/ %s: ${:.2f} | {} unidades = ${:.2f} (source={} ts={})".format(
                    cg_id, px, amount_native, usd_value, src, ts
                )
            )
            _price_cache_put(cache_key, px, from_backup=True)  # Cache por mais tempo
            return px, usd_value
        
        LOG.error("[price-fail] Falha ao obter preço para %s - configure COINGECKO_API_KEY", cg_id)
        return None

    px = float(data[cg_id]["usd"])
    usd_value = amount_native * px
    LOG.info(f"[LIVE-PRICE] ✅ {cg_id}: ${px:.2f} | {amount_native} unidades = ${usd_value:.2f}")
    _price_cache_put(cache_key, px)
    return px, usd_value


async def _usd_token(
    chain_id: str,
    token_addr: str,
    amount_raw: int,
    decimals: int,
    force_refresh: bool = False,
) -> Optional[Tuple[float, float]]:
    token_addr_lc = token_addr.lower()
    amount = float(amount_raw) / float(10 ** decimals)

    # 1) tenta mapeamento "nativo" (ex.: BTCB -> bitcoin)
    alt_cgid = KNOWN_TOKEN_TO_CGID.get(f"{chain_id}:{token_addr_lc}")
    if alt_cgid:
        cache_key = f"native:{alt_cgid}"
        # SEMPRE forçar busca de preços atuais na internet
        LOG.info(f"[LIVE-PRICE] Buscando preço atual de token mapeado {alt_cgid}...")
        data = await _cg_get(f"https://api.coingecko.com/api/v3/simple/price?ids={alt_cgid}&vs_currencies=usd")
        if data and alt_cgid in data and "usd" in data[alt_cgid]:
            px = float(data[alt_cgid]["usd"])
            usd_value = amount * px
            LOG.info(f"[LIVE-PRICE] ✅ Token {alt_cgid}: ${px:.2f} | {amount} unidades = ${usd_value:.2f}")
            _price_cache_put(cache_key, px)
            return px, usd_value
        
        # Fallback para rate limiting ou API indisponível
        fallback_price = FALLBACK_PRICES.get(alt_cgid)
        if fallback_price:
            px = float(fallback_price)
            usd_value = amount * px
            meta = FALLBACK_PRICE_META.get(alt_cgid, {})
            ts = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(meta.get("ts", 0)))
            src = meta.get("source", "manual")
            LOG.warning(
                f"[RATE-LIMIT-FALLBACK] Token fallback p/ alt_cgid {alt_cgid}: ${px:.2f} | {amount} unidades = ${usd_value:.2f} (source={src} ts={ts})"
            )
            _price_cache_put(cache_key, px)
            return px, usd_value
            
        LOG.info("[price] falhou alt_cgid=%s p/ token %s; tentando plataforma CG...", alt_cgid, token_addr_lc)

    # 2) fluxo padrão por plataforma/contrato
    platform = CHAINS[chain_id]["cg_platform"]
    cache_key = f"token:{platform}:{token_addr_lc}"
    
    # SEMPRE forçar busca de preços atuais na internet
    LOG.info(f"[LIVE-PRICE] Buscando preço atual de token {token_addr_lc} na plataforma {platform}...")
    data = await _cg_get(
        f"https://api.coingecko.com/api/v3/simple/token_price/{platform}"
        f"?contract_addresses={token_addr_lc}&vs_currencies=usd"
    )
    if data:
        for k, v in data.items():
            if k.lower() == token_addr_lc and "usd" in v:
                px = float(v["usd"])
                usd_value = amount * px
                LOG.info(f"[LIVE-PRICE] ✅ Token {token_addr_lc}: ${px:.2f} | {amount} unidades = ${usd_value:.2f}")
                _price_cache_put(cache_key, px)
                return px, usd_value

    # 3) fallback com cache expirado, se existir
    stale = _PRICE_CACHE.get(cache_key)
    if stale:
        px = float(stale[0])
        LOG.info("[price-fallback] usando cache expirado p/ %s: %f", cache_key, px)
        return px, amount * px

    # 4) Fallback para rate limiting ou API indisponível
    token_key = f"{chain_id}:{token_addr_lc}"
    fallback_price = FALLBACK_PRICES.get(token_key)
    if fallback_price:
        px = float(fallback_price)
        usd_value = amount * px
        meta = FALLBACK_PRICE_META.get(token_key, {})
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(meta.get("ts", 0)))
        src = meta.get("source", "manual")
        LOG.warning(
            f"[RATE-LIMIT-FALLBACK] Token fallback p/ {token_key}: ${px:.2f} | {amount} unidades = ${usd_value:.2f} (source={src} ts={ts})"
        )
        _price_cache_put(cache_key, px)
        return px, usd_value

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
async def _resolve_on_chain(
    w3: Web3, chain_id: str, tx_hash: str, force_refresh: bool = False
) -> Tuple[bool, str, Optional[float], Dict[str, Any]]:
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
        px = await _usd_native(chain_id, amount_native, force_refresh=force_refresh)
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

                px = await _usd_token(
                    chain_id, token_addr, value_raw, decimals, force_refresh=force_refresh
                )
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
        inp = tx.get("input") or ""
        # Converter para string se for bytes/HexBytes
        if isinstance(inp, bytes):
            inp = "0x" + inp.hex()
        elif hasattr(inp, 'hex'):
            inp = "0x" + inp.hex()
        inp_str = str(inp) if inp else ""

        if inp_str and inp_str.startswith("0xa9059cbb") and WALLET_ADDRESS:
            # 4 bytes sig + 32 bytes to + 32 bytes value
            if len(inp_str) >= 10 + 64 + 64:
                to_hex = inp_str[10 + (64 - 40):10 + 64]  # últimos 20 bytes do 1º arg
                toA = Web3.to_checksum_address("0x" + to_hex[-40:])
                value_hex = inp_str[10 + 64:10 + 64 + 64]
                value_raw = int(value_hex, 16)

                if toA.lower() == WALLET_ADDRESS.lower() and value_raw > 0:
                    token_addr = Web3.to_checksum_address(tx_to) if tx_to else None
                    if token_addr:
                        decimals = _erc20_decimals(w3, token_addr)
                        symbol = _erc20_symbol(w3, token_addr) or "TOKEN"
                        px = await _usd_token(
                            chain_id, token_addr, value_raw, decimals, force_refresh=force_refresh
                        )
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
    """Converte chain_id para nome legível"""
    chain_names = {
        "0x1": "Ethereum",
        "0x38": "BNB Smart Chain", 
        "0x89": "Polygon",
        "0xa4b1": "Arbitrum One",
        "0xa": "OP Mainnet",
        "0x2105": "Base",
        "0xa86a": "Avalanche",
        "0x144": "zkSync Era",
        "0xe708": "Linea",
        "0x13e31": "Blast",
        "0xa4ec": "Celo",
        "0x1388": "Mantle",
        "0xcc": "opBNB",
        "0x82750": "Scroll",
        "0xfa": "Fantom",
        "0x64": "Gnosis",
        "0x507": "Moonbeam", 
        "0x505": "Moonriver",
        "0x19": "Cronos",
        "0x7a69": "Zora",
        "0x1b3": "Ape Chain",
        "0x2710": "Morph"
    }
    return chain_names.get(chain_id, chain_id)


async def resolve_payment_usd_autochain(
    tx_hash: str, force_refresh: bool = False
) -> Tuple[bool, str, Optional[float], Dict[str, Any]]:
    """
    Procura a transação em TODAS as chains em PARALELO (muito mais rápido).
    Ao achar a tx em alguma delas, resolve e retorna.
    OTIMIZADO: Timeout total de 15 segundos para validação rápida.
    """
    # Verificar cache de transações validadas (otimização para escala)
    if not force_refresh:
        normalized_hash = tx_hash.lower().replace('0x', '')
        if len(normalized_hash) == 64:
            normalized_hash = '0x' + normalized_hash
            if normalized_hash in _TX_VALIDATION_CACHE:
                cached_ts, cached_result = _TX_VALIDATION_CACHE[normalized_hash]
                age = time.time() - cached_ts
                if age < TX_VALIDATION_TTL:
                    LOG.info(f"[TX-CACHE] ✅ Usando resultado cacheado para {tx_hash} (idade: {age:.0f}s)")
                    return cached_result
                else:
                    # Cache expirado, remover
                    del _TX_VALIDATION_CACHE[normalized_hash]
                    LOG.info(f"[TX-CACHE] Cache expirado para {tx_hash} (idade: {age:.0f}s)")

    LOG.info(f"[AUTOCHAIN] Procurando transação {tx_hash} em {len(CHAINS)} chains em PARALELO...")

    # Normalizar hash (remover 0x e garantir lowercase)
    clean_hash = tx_hash.lower().replace('0x', '')
    if len(clean_hash) != 64:
        LOG.error(f"[AUTOCHAIN] Hash inválido: {tx_hash} (tamanho: {len(clean_hash)})")
        return False, "Hash de transação inválido (deve ter 64 caracteres hex).", None, {}

    normalized_hash = '0x' + clean_hash

    # OTIMIZAÇÃO: Priorizar apenas as 3 chains mais usadas (ETH, BSC, Polygon)
    priority_chains = ["0x1", "0x38", "0x89"]  # ETH, BSC, Polygon
    other_chains = [cid for cid in CHAINS.keys() if cid not in priority_chains]
    ordered_chains = priority_chains + other_chains

    # Função auxiliar para buscar em uma chain
    async def try_chain(chain_id: str):
        chain_name = human_chain(chain_id)
        try:
            result = await _try_get_transaction_with_backup(chain_id, normalized_hash)
            if result:
                return chain_id, result, None
            return chain_id, None, "not_found"
        except Exception as e:
            return chain_id, None, str(e)[:80]

    # OTIMIZAÇÃO: Adicionar timeout total de 15 segundos
    async def search_with_timeout():
        # Buscar em PARALELO - primeiro nas prioritárias, depois nas outras
        LOG.info(f"[AUTOCHAIN] Buscando em chains prioritárias: {[human_chain(c) for c in priority_chains]}")

        # Fase 1: Testar chains prioritárias em paralelo
        priority_tasks = [try_chain(cid) for cid in priority_chains if cid in CHAINS]
        priority_results = await asyncio.gather(*priority_tasks, return_exceptions=True)

        return priority_results, other_chains

    try:
        # Timeout total de 15 segundos
        priority_results, other_chains = await asyncio.wait_for(search_with_timeout(), timeout=15.0)

        # Verificar se encontrou nas prioritárias
        for chain_id, result, error in priority_results:
            if isinstance((chain_id, result, error), Exception):
                continue
            if result:
                tx, w3 = result
                chain_name = human_chain(chain_id)
                LOG.info(f"[AUTOCHAIN] ✅ Transação encontrada em {chain_name}!")
                ok, msg, usd, details = await _resolve_on_chain(
                    w3, chain_id, normalized_hash, force_refresh=force_refresh
                )
                details['found_on_chain'] = chain_name
                details['search_time'] = 'fast'
                LOG.info(f"[RESULT {chain_name}] ok={ok} msg={msg} usd=${usd}")

                # Salvar no cache de transações
                result = (ok, msg, usd, details)
                _TX_VALIDATION_CACHE[normalized_hash] = (time.time(), result)
                LOG.info(f"[TX-CACHE] Resultado salvo no cache: {normalized_hash}")

                return ok, msg, usd, details

        # Fase 2: Se não encontrou nas prioritárias, NÃO buscar em outras chains (otimização)
        # Retornar erro rápido se não encontrou nas 3 principais
        LOG.warning(f"[AUTOCHAIN] Transação não encontrada nas chains prioritárias. Pulando busca em outras chains.")

    except asyncio.TimeoutError:
        LOG.error(f"[AUTOCHAIN] Timeout de 15s atingido ao buscar transação {tx_hash}")
        return False, "Validação expirou (timeout de 15s). Tente novamente.", None, {}

    # OTIMIZAÇÃO: Não buscar em outras chains, apenas nas 3 principais
    # Isso reduz drasticamente o tempo de validação (de 30s+ para ~5-10s)

    # Não encontrado nas chains prioritárias
    chains_tried = ', '.join([human_chain(cid) for cid in priority_chains])
    LOG.error(f"[AUTOCHAIN] Transação {tx_hash} não encontrada nas chains prioritárias ({chains_tried})")

    # Salvar resultado negativo no cache (evita re-buscar transações inválidas)
    result = (False, f"Transação não encontrada nas blockchains principais ({chains_tried}). Use Ethereum, BSC ou Polygon.", None, {
        'searched_chains': priority_chains,
        'tx_hash': normalized_hash,
        'total_chains': len(CHAINS)
    })
    _TX_VALIDATION_CACHE[normalized_hash] = (time.time(), result)
    LOG.info(f"[TX-CACHE] Resultado negativo salvo no cache: {normalized_hash}")

    return result


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
            f"• 30 dias: $30.00 USD (Mensal)\n"
            f"• 90 dias: $70.00 USD (Trimestral)\n"
            f"• 180 dias: $110.00 USD (Semestral)\n"
            f"• 365 dias: $179.00 USD (Anual)"
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
    
    # Verificar transação on-chain SEMPRE com preços atuais
    try:
        # SEMPRE usar force_refresh=True para garantir preços atualizados
        ok, msg_result, usd_paid, details = await resolve_payment_usd_autochain(
            tx_hash, force_refresh=True
        )
        
        LOG.info(f"[PRICE-CHECK] Verificação com preços atuais - Hash: {tx_hash[:12]}... USD: ${usd_paid:.4f}" if usd_paid else f"[PRICE-CHECK] Falha na verificação - Hash: {tx_hash[:12]}...")
        
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
                from utils import vip_upsert_and_get_until, create_one_time_invite
                vip_until = await vip_upsert_and_get_until(user.id, user.username, plan_days, user.first_name)

                # Tentar criar convite automático
                plan_names = {30: "Mensal", 90: "Trimestral", 180: "Semestral", 365: "Anual"}
                plan_name = plan_names.get(plan_days, f"{plan_days} dias")

                try:
                    from main import application, GROUP_VIP_ID
                    from utils import create_invite_link_flexible
                    invite_link = await create_invite_link_flexible(
                        application.bot, GROUP_VIP_ID, retries=3
                    )

                    if invite_link:
                        # Mensagem com convite
                        welcome_msg = (
                            f"🎉 <b>PAGAMENTO CONFIRMADO!</b>\n\n"
                            f"✅ Valor recebido: <b>${usd_paid:.2f} USD</b>\n"
                            f"👑 Plano ativado: <b>{plan_name} ({plan_days} dias)</b>\n"
                            f"📅 Válido até: <b>{vip_until.strftime('%d/%m/%Y')}</b>\n\n"
                            f"🔗 <b>Clique no link abaixo para entrar no grupo VIP:</b>\n"
                            f"{invite_link}\n\n"
                            f"⚠️ <b>IMPORTANTE:</b> Este link expira em 2 horas e tem apenas 1 uso.\n\n"
                            f"🎁 <b>Seja bem-vindo(a) ao VIP!</b>\n"
                            f"💎 Aproveite todo o conteúdo exclusivo!\n"
                            f"📬 Você receberá atualizações diárias de novos arquivos!\n\n"
                            f"Obrigado pela confiança! 🙏"
                        )
                    else:
                        # Mensagem sem convite
                        welcome_msg = (
                            f"🎉 <b>PAGAMENTO CONFIRMADO!</b>\n\n"
                            f"✅ Valor recebido: <b>${usd_paid:.2f} USD</b>\n"
                            f"👑 Plano ativado: <b>{plan_name} ({plan_days} dias)</b>\n"
                            f"📅 Válido até: <b>{vip_until.strftime('%d/%m/%Y')}</b>\n\n"
                            f"📬 Entre em contato para receber o convite do grupo VIP.\n\n"
                            f"Obrigado pela preferência! 🙏"
                        )
                except Exception as e:
                    LOG.warning(f"Falha ao gerar convite no comando /tx: {e}")
                    # Mensagem de fallback
                    welcome_msg = (
                        f"🎉 <b>PAGAMENTO CONFIRMADO!</b>\n\n"
                        f"✅ Valor recebido: <b>${usd_paid:.2f} USD</b>\n"
                        f"👑 Plano ativado: <b>{plan_name} ({plan_days} dias)</b>\n"
                        f"📅 Válido até: <b>{vip_until.strftime('%d/%m/%Y')}</b>\n\n"
                        f"📬 Aguarde o convite do grupo VIP em breve!\n\n"
                        f"Obrigado! 🙏"
                    )

                return await msg.reply_text(welcome_msg, parse_mode="HTML")
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
            # Usar first_name se disponível no payment, senão usar None
            first_name = getattr(p, 'first_name', None)
            vip_until = await vip_upsert_and_get_until(p.user_id, p.username, p.days, first_name)
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
    try:
        from main import SessionLocal, Payment, GROUP_VIP_ID, application
        bot_available = True
    except Exception as e:
        LOG.warning(f"Bot não disponível (normal se BOT_TOKEN não configurado): {e}")
        # Imports essenciais sem bot
        try:
            import sys
            import os
            sys.path.append(os.path.dirname(__file__))
            from models import Payment
            from db import SessionLocal
            GROUP_VIP_ID = int(os.getenv("GROUP_VIP_ID", "-1003255098941"))  # Valor padrão atualizado
            application = None
            bot_available = False
        except Exception as e2:
            LOG.error(f"Falha ao importar dependências básicas: {e2}")
            return False, f"Erro de configuração: {e2}", {"error": "config_error"}
    
    from utils import create_one_time_invite, vip_upsert_and_get_until, choose_plan_from_usd
    import datetime as dt
    
    # Verificar se hash já existe
    with SessionLocal() as s:
        existing = s.query(Payment).filter(Payment.tx_hash == tx_hash).first()
        if existing:
            return False, "Hash já usada", {"error": "hash_used"}

    # Resolver pagamento SEMPRE com preços atuais para aprovação justa
    ok, info, usd, details = await resolve_payment_usd_autochain(
        tx_hash, force_refresh=True
    )
    
    LOG.info(f"[MANUAL-APPROVAL] Aprovação com preços atuais - Hash: {tx_hash[:12]}... USD: ${float(usd):.4f}" if usd else f"[MANUAL-APPROVAL] Falha na aprovação - Hash: {tx_hash[:12]}...")
    if not ok:
        return False, info, {"details": details}

    # Verificar se valor cobre algum plano baseado no valor real (sem preços estáticos)
    days = choose_plan_from_usd(usd or 0.0)
    if not days:
        return False, f"Valor insuficiente (${float(usd):.2f})", {"details": details, "usd": usd}

    # Verificar se é UID temporário (formato antigo "temp_*" ou novo timestamp)
    is_temp_uid = False
    if isinstance(tg_id, str) and tg_id.startswith("temp_"):
        is_temp_uid = True
    elif isinstance(tg_id, (int, str)):
        # Verificar se é um timestamp (UID temporário numérico)
        # UIDs do Telegram são tipicamente menores que 2^31 (2147483647)
        # Timestamps são maiores que 1600000000 (2020) e menores que 2000000000 (2033)
        uid_num = int(tg_id) if isinstance(tg_id, str) else tg_id
        if 1600000000 <= uid_num <= 2000000000:
            is_temp_uid = True
            LOG.info(f"[INVITE-DEBUG] UID detectado como timestamp temporário: {uid_num}")

    actual_tg_id = None
    until = None
    link = None
    
    LOG.info(f"[INVITE-DEBUG] UID recebido: '{tg_id}' (tipo: {type(tg_id)}) | is_temp_uid: {is_temp_uid}")
    
    if not is_temp_uid:
        try:
            actual_tg_id = int(tg_id)
            LOG.info(f"[INVITE-DEBUG] UID convertido para int: {actual_tg_id}")
            
            # Estender VIP apenas se for ID real
            until = await vip_upsert_and_get_until(actual_tg_id, username, days, None)
            LOG.info(f"[INVITE-DEBUG] VIP estendido até: {until}")
            
            # Gerar convite - tentar múltiplas estratégias
            if bot_available and application and application.bot:
                try:
                    from utils import create_invite_link_flexible
                    LOG.info(f"[INVITE-DEBUG] Tentando gerar convite para canal {GROUP_VIP_ID}")
                    link = await create_invite_link_flexible(application.bot, GROUP_VIP_ID, retries=3)
                    if link:
                        LOG.info(f"[INVITE-DEBUG] ✅ Convite gerado com sucesso: {link[:50]}...")
                    else:
                        LOG.warning(f"[INVITE-DEBUG] ❌ Não foi possível gerar convite")
                except Exception as e:
                    LOG.error(f"[INVITE-DEBUG] ❌ Erro ao gerar convite: {e}", exc_info=True)
                    link = None
            else:
                LOG.warning(f"[INVITE-DEBUG] Bot não disponível, pulando geração de convite")
                link = None
        except (ValueError, TypeError) as e:
            LOG.warning(f"[INVITE-DEBUG] Erro ao processar UID, tratando como temporário: {e}")
            is_temp_uid = True

    # Salvar pagamento
    with SessionLocal() as s:
        # Extrair informações do payment para salvar
        token_symbol = details.get("token_symbol", "Unknown")
        token_amount = details.get("amount_human", details.get("amount", "N/A"))
        
        user_id_to_save = actual_tg_id if actual_tg_id else 0
        LOG.info(f"[PAYMENT-SAVE] Salvando pagamento - actual_tg_id: {actual_tg_id}, user_id_to_save: {user_id_to_save}, is_temp_uid: {is_temp_uid}")

        p = Payment(
            tx_hash=tx_hash,
            user_id=user_id_to_save,  # 0 para pagamentos sem ID válido
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

    LOG.info(f"[INVITE-DEBUG] Finalizando: is_temp_uid={is_temp_uid}, link={link is not None if link else False}")
    
    # Calcular data de expiração do VIP
    vip_until_str = until.strftime('%d/%m/%Y') if until else "N/A"

    # Criar mensagem de boas-vindas personalizada
    plan_names = {30: "Mensal", 90: "Trimestral", 180: "Semestral", 365: "Anual"}
    plan_name = plan_names.get(days, f"{days} dias")

    if is_temp_uid:
        # Para UIDs temporários, ainda gerar convite automático
        # O ID será capturado quando o usuário entrar no grupo
        if bot_available and application and application.bot:
            try:
                from utils import create_invite_link_flexible
                link = await create_invite_link_flexible(application.bot, GROUP_VIP_ID, retries=3)
                LOG.info(f"[INVITE-DEBUG] Convite temporário gerado: {link is not None}")
                if link:
                    msg = (
                        f"🎉 <b>PAGAMENTO CONFIRMADO!</b>\n\n"
                        f"✅ Valor recebido: <b>${float(usd):.2f} USD</b>\n"
                        f"👑 Plano ativado: <b>{plan_name} ({days} dias)</b>\n"
                        f"📅 Válido até: <b>{vip_until_str}</b>\n\n"
                        f"🔗 <b>Clique no link abaixo para entrar no grupo VIP:</b>\n"
                        f"{link}\n\n"
                        f"⚠️ <b>IMPORTANTE:</b> Este link expira em 2 horas e tem apenas 1 uso.\n\n"
                        f"🎁 Seja bem-vindo(a) ao VIP! Aproveite o conteúdo exclusivo!"
                    )
                    return True, msg, {"invite": link, "usd": usd, "days": days, "temp_uid": True}
            except Exception as e:
                LOG.warning(f"[INVITE-DEBUG] Falha ao gerar convite temporário: {e}")

        # Fallback se não conseguir gerar convite
        msg = (
            f"🎉 <b>PAGAMENTO CONFIRMADO!</b>\n\n"
            f"✅ Valor recebido: <b>${float(usd):.2f} USD</b>\n"
            f"👑 Plano ativado: <b>{plan_name} ({days} dias)</b>\n"
            f"📅 Válido até: <b>{vip_until_str}</b>\n\n"
            f"⚠️ Para receber o convite do grupo VIP, entre em contato conosco fornecendo seu ID do Telegram.\n\n"
            f"Obrigado pela preferência! 🙏"
        )
        return True, msg, {"usd": usd, "days": days, "temp_uid": True}
    else:
        if link:
            msg = (
                f"🎉 <b>PAGAMENTO CONFIRMADO!</b>\n\n"
                f"✅ Valor recebido: <b>${float(usd):.2f} USD</b>\n"
                f"👑 Plano ativado: <b>{plan_name} ({days} dias)</b>\n"
                f"📅 Válido até: <b>{vip_until_str}</b>\n\n"
                f"🔗 <b>Clique no link abaixo para entrar no grupo VIP:</b>\n"
                f"{link}\n\n"
                f"⚠️ <b>IMPORTANTE:</b> Este link expira em 2 horas e tem apenas 1 uso.\n\n"
                f"🎁 <b>Seja bem-vindo(a) ao VIP!</b>\n"
                f"💎 Aproveite todo o conteúdo exclusivo!\n"
                f"📬 Você receberá atualizações diárias de novos arquivos!\n\n"
                f"Obrigado pela confiança! 🙏"
            )
            payload = {"invite": link, "until": until.isoformat(), "usd": usd, "days": days}
            LOG.info(f"[INVITE-DEBUG] Retornando com convite automático")
        else:
            msg = (
                f"🎉 <b>PAGAMENTO CONFIRMADO!</b>\n\n"
                f"✅ Valor recebido: <b>${float(usd):.2f} USD</b>\n"
                f"👑 Plano ativado: <b>{plan_name} ({days} dias)</b>\n"
                f"📅 Válido até: <b>{vip_until_str}</b>\n\n"
                f"⚠️ <b>VIP ATIVADO COM SUCESSO!</b>\n"
                f"📬 Entre em contato para receber o convite do grupo VIP.\n\n"
                f"🎁 <b>Benefícios do seu plano:</b>\n"
                f"• Acesso a conteúdo exclusivo premium\n"
                f"• Atualizações diárias de arquivos\n"
                f"• Suporte prioritário\n\n"
                f"Obrigado pela preferência! 🙏"
            )
            payload = {"no_auto_invite": True, "until": until.isoformat(), "usd": usd, "days": days}
            LOG.info(f"[INVITE-DEBUG] Retornando sem convite automático")

        if notify_user and actual_tg_id and bot_available and application and application.bot:
            try:
                await application.bot.send_message(
                    chat_id=actual_tg_id,
                    text=msg,
                    parse_mode="HTML"
                )
                LOG.info(f"[NOTIFY] ✅ Mensagem de boas-vindas enviada para user {actual_tg_id}")

                # Enviar log de sucesso para grupo de logs
                try:
                    from main import LOGS_GROUP_ID
                    log_msg = (
                        f"✅ <b>MENSAGEM DE BOAS-VINDAS ENVIADA</b>\n"
                        f"👤 User: <code>{actual_tg_id}</code> (@{username or 'sem_username'})\n"
                        f"💰 Valor: ${float(usd):.2f} USD\n"
                        f"📅 Plano: {plan_name} ({days} dias)\n"
                        f"⏰ VIP até: {until.strftime('%d/%m/%Y %H:%M') if until else 'N/A'}\n"
                        f"🔗 Link gerado: {'Sim' if link else 'Não'}"
                    )
                    await application.bot.send_message(
                        chat_id=LOGS_GROUP_ID,
                        text=log_msg,
                        parse_mode="HTML"
                    )
                except Exception as log_error:
                    LOG.warning(f"[NOTIFY] Erro ao enviar log de sucesso: {log_error}")
            except Exception as e:
                LOG.warning(f"[NOTIFY] ❌ Falha ao enviar mensagem (usuário não iniciou conversa): {e}")
                # Salvar mensagem pendente para enviar quando o usuário der /start ou entrar no grupo
                try:
                    from models import PendingNotification
                    from main import LOGS_GROUP_ID

                    with SessionLocal() as s:
                        pending = PendingNotification(
                            user_id=actual_tg_id,
                            username=username,
                            message=msg
                        )
                        s.add(pending)
                        s.commit()
                    LOG.info(f"[NOTIFY] 📝 Mensagem salva como pendente para user {actual_tg_id}")

                    # Enviar log para grupo de logs
                    try:
                        log_msg = (
                            f"📝 <b>MENSAGEM PENDENTE SALVA</b>\n"
                            f"👤 User: <code>{actual_tg_id}</code> (@{username or 'sem_username'})\n"
                            f"💰 Valor: ${float(usd):.2f} USD\n"
                            f"📅 Plano: {plan_name} ({days} dias)\n"
                            f"⏰ VIP até: {until.strftime('%d/%m/%Y %H:%M') if until else 'N/A'}\n\n"
                            f"ℹ️ Mensagem será enviada quando o usuário entrar no grupo VIP"
                        )
                        await application.bot.send_message(
                            chat_id=LOGS_GROUP_ID,
                            text=log_msg,
                            parse_mode="HTML"
                        )
                    except Exception as log_error:
                        LOG.warning(f"[NOTIFY] Erro ao enviar log: {log_error}")

                except Exception as save_error:
                    LOG.error(f"[NOTIFY] Erro ao salvar mensagem pendente: {save_error}")

        return True, msg, payload

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
