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

# Preços centralizados — altere SOMENTE em config.py
from config import VIP_PRICES as DEFAULT_VIP_PRICES_USD

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
    "tether": 1.0,  # USDT
    "usd-coin": 1.0,  # USDC

    # USDC em múltiplas redes
    "0x1:0xa0b86991c6e31cc170c8b9e71b51e1a53af4e9b8c9e": 1.0,     # USDC na Ethereum
    "0x38:0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d": 1.0,     # USDC na BSC
    "0x89:0x2791bca1f2de4661ed88a30c99a7a9449aa84174": 1.0,     # USDC na Polygon
    "0xa4b1:0xaf88d065e77c8cc2239327c5edb3a432268e5831": 1.0,   # USDC na Arbitrum
    "0xa:0x0b2c639c533813f4aa9d7837caf62653d097ff85": 1.0,      # USDC na Optimism
    "0x2105:0x833589fcd6edb6e08f4c7c32d4f71b54bda02913": 1.0,   # USDC na Base

    # USDT em múltiplas redes
    "0x1:0xdac17f958d2ee523a2206206994597c13d831ec7": 1.0,      # USDT na Ethereum (ERC-20)
    "0x38:0x55d398326f99059ff775485246999027b3197955": 1.0,     # USDT na BSC (BEP-20)
    "0x89:0xc2132d05d31c914a87c6611c10748aeb04b58e8f": 1.0,     # USDT na Polygon
}

# Metadados para auditoria dos preços de fallback
FALLBACK_PRICE_META: Dict[str, Dict[str, Any]] = {
    k: {"source": "manual", "ts": time.time()} for k in FALLBACK_PRICES
}


def _update_fallback_prices() -> None:
    """Atualiza preços de fallback via Binance (primário) → Kraken → CoinGecko (backup).

    Também popula _PRICE_CACHE para que validações usem cache instantâneo.
    """

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

    # Mapeamento Binance: cg_id -> par Binance
    binance_pairs = {
        "bitcoin": "BTCUSDT",
        "ethereum": "ETHUSDT",
        "binancecoin": "BNBUSDT",
        "avalanche-2": "AVAXUSDT",
        "fantom": "FTMUSDT",
        "apecoin": "APEUSDT",
        "mantle": "MNTUSDT",
        "celo": "CELOUSDT",
        "polygon-pos": "MATICUSDT",
    }

    # Mapeamento Kraken: cg_id -> par Kraken
    kraken_pairs = {
        "bitcoin": "XBTUSD",
        "ethereum": "ETHUSD",
        "avalanche-2": "AVAXUSD",
        "fantom": "FTMUSD",
        "apecoin": "APEUSD",
        "polygon-pos": "MATICUSD",
    }

    updated_count = 0
    resolved: dict[str, float] = {}  # cg_id -> price

    # --- Fonte 1: Binance (sem rate limit, gratuita) ---
    binance_ids = [item["cg_id"] for item in price_updates if item["cg_id"] in binance_pairs]
    if binance_ids:
        # Buscar todos os preços de uma vez via /ticker/price (aceita múltiplos símbolos)
        symbols_json = str([binance_pairs[cid] for cid in binance_ids]).replace("'", '"')
        url = f"https://api.binance.com/api/v3/ticker/price?symbols={symbols_json}"
        try:
            r = httpx.get(url, timeout=10)
            if r.status_code == 200:
                data = r.json()
                # Criar mapa reverso pair->cg_id
                pair_to_cg = {v: k for k, v in binance_pairs.items()}
                for entry in data:
                    sym = entry.get("symbol", "")
                    cg_id = pair_to_cg.get(sym)
                    if cg_id and entry.get("price"):
                        resolved[cg_id] = float(entry["price"])
                LOG.info("[AUTO-UPDATE] Binance retornou %d preços", len([e for e in data if pair_to_cg.get(e.get("symbol"))]))
            else:
                LOG.warning("[AUTO-UPDATE] Binance HTTP %d", r.status_code)
        except Exception as exc:
            LOG.warning("[AUTO-UPDATE] Binance falhou: %s", exc)

    # --- Fonte 2: Kraken para tokens não resolvidos ---
    missing_kraken = [cid for cid in kraken_pairs if cid not in resolved]
    for cg_id in missing_kraken:
        pair = kraken_pairs[cg_id]
        url = f"https://api.kraken.com/0/public/Ticker?pair={pair}"
        try:
            r = httpx.get(url, timeout=8)
            if r.status_code == 200:
                data = r.json()
                if data and "result" in data and data["result"]:
                    px = float(list(data["result"].values())[0]["c"][0])
                    if px > 0:
                        resolved[cg_id] = px
                        LOG.info("[AUTO-UPDATE] Kraken %s = $%.2f", cg_id, px)
        except Exception as exc:
            LOG.warning("[AUTO-UPDATE] Kraken falhou p/ %s: %s", cg_id, exc)

    # --- Fonte 3: CoinGecko como backup para tokens ainda não resolvidos ---
    missing_cg = [item["cg_id"] for item in price_updates if item["cg_id"] not in resolved]
    if missing_cg:
        coin_ids_str = ",".join(missing_cg)
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin_ids_str}&vs_currencies=usd"
        try:
            r = httpx.get(url, timeout=15)
            if r.status_code == 200:
                data = r.json()
                for cg_id in missing_cg:
                    if cg_id in data and "usd" in data[cg_id]:
                        resolved[cg_id] = float(data[cg_id]["usd"])
                LOG.info("[AUTO-UPDATE] CoinGecko retornou %d preços (backup)", sum(1 for cid in missing_cg if cid in resolved))
            elif r.status_code == 429:
                LOG.warning("[AUTO-UPDATE] CoinGecko 429 (rate limit) — ignorado, Binance/Kraken já cobriram")
            else:
                LOG.warning("[AUTO-UPDATE] CoinGecko erro %d", r.status_code)
        except Exception as exc:
            LOG.warning("[AUTO-UPDATE] CoinGecko falhou: %s", exc)

    # --- Aplicar preços resolvidos em FALLBACK_PRICES + _PRICE_CACHE ---
    for item in price_updates:
        cg_id = item["cg_id"]
        if cg_id not in resolved:
            continue
        px = resolved[cg_id]

        for key in item["keys"]:
            old_price = FALLBACK_PRICES.get(key, 0)
            FALLBACK_PRICES[key] = px
            FALLBACK_PRICE_META[key] = {"source": "auto_binance_kraken", "ts": time.time()}
            updated_count += 1
            LOG.info(
                "[AUTO-UPDATE] %s: $%.2f -> $%.2f (key: %s)",
                item["name"], old_price, px, key
            )

        # Popular _PRICE_CACHE para que validações usem cache instantâneo
        _price_cache_put(f"native:{cg_id}", px, from_backup=True)

    LOG.info(
        "[AUTO-UPDATE] Atualizados %d preços de fallback em %s (fontes: Binance+Kraken+CoinGecko)",
        updated_count,
        time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
    )


# Atualizar preços de fallback no startup
_update_fallback_prices()


# =========================
# Atualização Periódica de Preços de Fallback
# =========================
import threading

def _periodic_price_update():
    """Atualiza preços de fallback a cada 5 minutos via Binance/Kraken (sem rate limit)"""
    while True:
        try:
            time.sleep(300)  # 5 minutos — Binance/Kraken não têm rate limit restritivo
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
# BTCB (BSC) -> bitcoin, Stablecoins -> tether/usd-coin
KNOWN_TOKEN_TO_CGID = {
    # chainId:tokenAddress -> cg_id
    # Bitcoin wrapped
    f"0x38:{'0x7130d2a12b9bcbfae4f2634d864a1ee1ce3ead9c'}": "bitcoin",  # BTCB na BSC

    # Stablecoins - USDT
    f"0x1:{'0xdac17f958d2ee523a2206206994597c13d831ec7'}": "tether",    # USDT na Ethereum (ERC-20)
    f"0x38:{'0x55d398326f99059ff775485246999027b3197955'}": "tether",   # USDT na BSC (BEP-20)
    f"0x89:{'0xc2132d05d31c914a87c6611c10748aeb04b58e8f'}": "tether",   # USDT na Polygon

    # Stablecoins - USDC
    f"0x1:{'0xa0b86991c6e31cc170c8b9e71b51e1a53af4e9b8c9e'}": "usd-coin",  # USDC na Ethereum (ERC-20)
    f"0x38:{'0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d'}": "usd-coin",   # USDC na BSC (BEP-20)
    f"0x89:{'0x2791bca1f2de4661ed88a30c99a7a9449aa84174'}": "usd-coin",   # USDC na Polygon
    f"0xa4b1:{'0xaf88d065e77c8cc2239327c5edb3a432268e5831'}": "usd-coin", # USDC na Arbitrum
    f"0xa:{'0x0b2c639c533813f4aa9d7837caf62653d097ff85'}": "usd-coin",    # USDC na Optimism
    f"0x2105:{'0x833589fcd6edb6e08f4c7c32d4f71b54bda02913'}": "usd-coin", # USDC na Base
}

# Mapeamento de endereços para símbolos conhecidos (fallback)
KNOWN_TOKEN_SYMBOLS = {
    # Bitcoin wrapped
    "0x7130d2a12b9bcbfae4f2634d864a1ee1ce3ead9c": "BTCB",  # BTCB na BSC

    # USDC em várias redes
    "0xa0b86991c6e31cc170c8b9e71b51e1a53af4e9b8c9e": "USDC",  # USDC na Ethereum
    "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d": "USDC",     # USDC na BSC
    "0x2791bca1f2de4661ed88a30c99a7a9449aa84174": "USDC",     # USDC na Polygon
    "0xaf88d065e77c8cc2239327c5edb3a432268e5831": "USDC",     # USDC na Arbitrum
    "0x0b2c639c533813f4aa9d7837caf62653d097ff85": "USDC",     # USDC na Optimism
    "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913": "USDC",     # USDC na Base

    # USDT em várias redes
    "0xdac17f958d2ee523a2206206994597c13d831ec7": "USDT",     # USDT na Ethereum
    "0x55d398326f99059ff775485246999027b3197955": "USDT",     # USDT na BSC
    "0xc2132d05d31c914a87c6611c10748aeb04b58e8f": "USDT",     # USDT na Polygon
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

# APIs de backup GRATUITAS para preços de crypto (principais moedas)
# Binance → Kraken → CoinGecko → Fallback Prices
BACKUP_PRICE_APIS = {
    # Binance - API pública grátis (pares principais: BTC, ETH, BNB, USDT)
    "binance": {
        "pairs": {
            "ethereum": "ETHUSDT",
            "bitcoin": "BTCUSDT",
            "binancecoin": "BNBUSDT",
            "tether": "USDTUSD",      # Preço do USDT (sempre ~1.00)
            "usd-coin": "USDCUSDT"    # Preço do USDC (sempre ~1.00)
        },
        "url_template": "https://api.binance.com/api/v3/ticker/price?symbol={pair}",
        "parser": lambda x: float(x.get("price", 0)) if x and "price" in x else None
    },
    # Kraken - API pública grátis, muito confiável
    "kraken": {
        "pairs": {
            "ethereum": "ETHUSD",
            "bitcoin": "XBTUSD",      # BTC = XBT no Kraken
            "binancecoin": "BNBUSD",
            "tether": "USDTUSD",
            "usd-coin": "USDCUSD"
        },
        "url_template": "https://api.kraken.com/0/public/Ticker?pair={pair}",
        "parser": lambda x: float(list(x["result"].values())[0]["c"][0]) if x and "result" in x and x["result"] else None
    }
}

async def _try_backup_apis(asset: str) -> Optional[float]:
    """Tenta obter preço via APIs GRATUITAS (Binance, Kraken)"""

    # 1) Tentar Binance primeiro (rápida e confiável)
    if asset in BACKUP_PRICE_APIS["binance"]["pairs"]:
        pair = BACKUP_PRICE_APIS["binance"]["pairs"][asset]
        url = BACKUP_PRICE_APIS["binance"]["url_template"].format(pair=pair)
        try:
            LOG.info(f"[BINANCE] Consultando Binance para {asset} ({pair})...")
            async with httpx.AsyncClient(timeout=5) as cli:
                r = await cli.get(url)
                if r.status_code == 200:
                    data = r.json()
                    price = BACKUP_PRICE_APIS["binance"]["parser"](data)
                    if price and price > 0:
                        price = float(price)
                        LOG.info(f"[BINANCE] ✅ {asset} = ${price:.2f}")
                        return price
                else:
                    LOG.warning(f"[BINANCE] HTTP {r.status_code}")
        except Exception as e:
            LOG.warning(f"[BINANCE] Erro: {str(e)[:60]}")

    # 2) Tentar Kraken como backup
    if asset in BACKUP_PRICE_APIS["kraken"]["pairs"]:
        pair = BACKUP_PRICE_APIS["kraken"]["pairs"][asset]
        url = BACKUP_PRICE_APIS["kraken"]["url_template"].format(pair=pair)
        try:
            LOG.info(f"[KRAKEN] Consultando Kraken para {asset} ({pair})...")
            async with httpx.AsyncClient(timeout=5) as cli:
                r = await cli.get(url)
                if r.status_code == 200:
                    data = r.json()
                    price = BACKUP_PRICE_APIS["kraken"]["parser"](data)
                    if price and price > 0:
                        price = float(price)
                        LOG.info(f"[KRAKEN] ✅ {asset} = ${price:.2f}")
                        return price
                else:
                    LOG.warning(f"[KRAKEN] HTTP {r.status_code}")
        except Exception as e:
            LOG.warning(f"[KRAKEN] Erro: {str(e)[:60]}")

    LOG.warning(f"[BACKUP] Todas as APIs gratuitas falharam para {asset}")
    return None

async def _cg_get(url: str) -> Optional[dict]:
    headers = {}
    if COINGECKO_API_KEY:
        headers["x-cg-pro-api-key"] = COINGECKO_API_KEY

    delay = PRICE_RETRY_BASE_DELAY
    last_err = None
    
    for attempt in range(1, PRICE_MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=8) as cli:
                r = await cli.get(url, headers=headers)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                LOG.warning("Coingecko 429 (rate-limit). attempt=%d url=%s", attempt, url)
                LOG.warning("[RATE-LIMIT] 429 recebido, usando fallbacks imediatamente")
                break
            last_err = f"{r.status_code} {r.text[:100]}"
            await asyncio.sleep(min(delay, 5))
            delay *= 2
        except Exception as e:
            last_err = str(e)[:100]
            await asyncio.sleep(min(delay, 5))
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
                "[STATIC-FALLBACK] CoinGecko + backup APIs indisponíveis, usando preço estático p/ %s: $%.2f | %s unidades = $%.2f (source=%s ts=%s)",
                cg_id, px, amount_native, usd_value, src, ts
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

        # Tentar cache primeiro (preenchido pelo background updater a cada 5 min)
        cached = _price_cache_get(cache_key, force_refresh=False, allow_extended=True)
        if cached is not None:
            px = float(cached)
            usd_value = amount * px
            return px, usd_value

        # Cache miss — tentar Binance/Kraken antes do CoinGecko
        LOG.info(f"[CACHE-MISS] Token mapeado {alt_cgid} — tentando APIs de backup...")
        backup_price = await _try_backup_apis(alt_cgid)
        if backup_price:
            px = backup_price
            usd_value = amount * px
            LOG.info(f"[BACKUP-SUCCESS] ✅ Token {alt_cgid}: ${px:.2f} | {amount} unidades = ${usd_value:.2f}")
            _price_cache_put(cache_key, px, from_backup=True)
            return px, usd_value

        # Último recurso: CoinGecko com timeout curto
        LOG.info(f"[CACHE-MISS] Tentando CoinGecko para {alt_cgid}...")
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

    # Tentar cache primeiro
    cached = _price_cache_get(cache_key, force_refresh=False, allow_extended=True)
    if cached is not None:
        px = float(cached)
        usd_value = amount * px
        return px, usd_value

    # Cache miss — chamar CoinGecko
    LOG.info(f"[CACHE-MISS] Token {token_addr_lc} na plataforma {platform} — buscando preço...")
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
    tx_hash: str, force_refresh: bool = False, progress_cb=None
) -> Tuple[bool, str, Optional[float], Dict[str, Any]]:
    """
    Procura a transação em TODAS as chains em PARALELO (muito mais rápido).
    Ao achar a tx em alguma delas, resolve e retorna.
    OTIMIZADO: Timeout total de 15 segundos para validação rápida.

    progress_cb: coroutine opcional (async def cb(text)) para atualizar progresso no Telegram.
    """

    async def _progress(text: str) -> None:
        if progress_cb:
            await progress_cb(text)

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
    await _progress(
        "🔍 <b>Validando transação...</b>\n\n"
        "⏳ Etapa 1/4 — Localizando transação nas blockchains..."
    )

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
                await _progress(
                    "🔍 <b>Validando transação...</b>\n\n"
                    f"✅ Etapa 2/4 — Transação encontrada na <b>{chain_name}</b>\n"
                    "⏳ Etapa 3/4 — Verificando confirmações e dados..."
                )
                ok, msg, usd, details = await _resolve_on_chain(
                    w3, chain_id, normalized_hash, force_refresh=force_refresh
                )
                details['found_on_chain'] = chain_name
                details['search_time'] = 'fast'
                LOG.info(f"[RESULT {chain_name}] ok={ok} msg={msg} usd=${usd}")

                if ok and usd:
                    await _progress(
                        "🔍 <b>Validando transação...</b>\n\n"
                        f"✅ Etapa 2/4 — Transação encontrada na <b>{chain_name}</b>\n"
                        "✅ Etapa 3/4 — Confirmações e dados verificados\n"
                        f"✅ Etapa 4/4 — Valor calculado: <b>${float(usd):.2f} USD</b>\n\n"
                        "🎉 Finalizando..."
                    )
                elif not ok:
                    await _progress(
                        "🔍 <b>Validando transação...</b>\n\n"
                        f"✅ Etapa 2/4 — Transação encontrada na <b>{chain_name}</b>\n"
                        f"❌ Etapa 3/4 — {msg}"
                    )

                # Salvar no cache de transações
                result = (ok, msg, usd, details)
                _TX_VALIDATION_CACHE[normalized_hash] = (time.time(), result)
                LOG.info(f"[TX-CACHE] Resultado salvo no cache: {normalized_hash}")

                return ok, msg, usd, details

        # Fase 2: Se não encontrou nas prioritárias, buscar nas outras chains
        LOG.info(f"[AUTOCHAIN] Não encontrado nas prioritárias. Buscando em {len(other_chains)} chains restantes...")
        await _progress(
            "🔍 <b>Validando transação...</b>\n\n"
            "⚠️ Etapa 1/4 — Não encontrada nas redes principais\n"
            f"⏳ Etapa 2/4 — Buscando em {len(other_chains)} redes adicionais..."
        )
        if other_chains:
            other_tasks = [try_chain(cid) for cid in other_chains]
            other_results = await asyncio.gather(*other_tasks, return_exceptions=True)

            for chain_id, result, error in other_results:
                if isinstance((chain_id, result, error), Exception):
                    continue
                if result:
                    tx, w3 = result
                    chain_name = human_chain(chain_id)
                    LOG.info(f"[AUTOCHAIN] ✅ Transação encontrada em {chain_name}!")
                    await _progress(
                        "🔍 <b>Validando transação...</b>\n\n"
                        f"✅ Etapa 2/4 — Transação encontrada na <b>{chain_name}</b>\n"
                        "⏳ Etapa 3/4 — Verificando confirmações e dados..."
                    )
                    ok, msg, usd, details = await _resolve_on_chain(
                        w3, chain_id, normalized_hash, force_refresh=force_refresh
                    )
                    details['found_on_chain'] = chain_name
                    details['search_time'] = 'extended'
                    LOG.info(f"[RESULT {chain_name}] ok={ok} msg={msg} usd=${usd}")

                    if ok and usd:
                        await _progress(
                            "🔍 <b>Validando transação...</b>\n\n"
                            f"✅ Etapa 2/4 — Transação encontrada na <b>{chain_name}</b>\n"
                            "✅ Etapa 3/4 — Confirmações e dados verificados\n"
                            f"✅ Etapa 4/4 — Valor calculado: <b>${float(usd):.2f} USD</b>\n\n"
                            "🎉 Finalizando..."
                        )
                    elif not ok:
                        await _progress(
                            "🔍 <b>Validando transação...</b>\n\n"
                            f"✅ Etapa 2/4 — Transação encontrada na <b>{chain_name}</b>\n"
                            f"❌ Etapa 3/4 — {msg}"
                        )

                    # Salvar no cache de transações
                    result = (ok, msg, usd, details)
                    _TX_VALIDATION_CACHE[normalized_hash] = (time.time(), result)
                    LOG.info(f"[TX-CACHE] Resultado salvo no cache: {normalized_hash}")

                    return ok, msg, usd, details

    except asyncio.TimeoutError:
        LOG.error(f"[AUTOCHAIN] Timeout de 15s atingido ao buscar transação {tx_hash}")
        return False, "Validação expirou (timeout de 15s). Tente novamente.", None, {}

    # Não encontrado em nenhuma chain
    chains_tried = ', '.join([human_chain(cid) for cid in ordered_chains[:10]])
    if len(ordered_chains) > 10:
        chains_tried += f" e mais {len(ordered_chains) - 10}..."

    LOG.error(f"[AUTOCHAIN] Transação {tx_hash} não encontrada em {len(CHAINS)} chains")

    # Salvar resultado negativo no cache (evita re-buscar transações inválidas)
    result = (False, f"Transação não encontrada em {len(CHAINS)} blockchains suportadas ({chains_tried}).", None, {
        'searched_chains': list(CHAINS.keys()),
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

        from config import vip_plans_text_usd
        checkout_msg = (
            f"💸 <b>Pagamento VIP via Cripto</b>\n\n"
            f"✅ Clique no botão abaixo para acessar nossa página de checkout segura\n"
            f"🔒 Pague com qualquer criptomoeda\n"
            f"⚡ Ativação automática após confirmação\n\n"
            f"💰 <b>Planos disponíveis:</b>\n"
            f"{vip_plans_text_usd()}"
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
    
    # Mensagem de progresso para o usuário acompanhar em tempo real
    status_msg = await msg.reply_text(
        "🔍 <b>Validando transação...</b>\n\n"
        "⏳ Etapa 1/4 — Localizando transação nas blockchains...",
        parse_mode="HTML",
    )

    async def _update_status(text: str) -> None:
        """Atualiza a mensagem de progresso (ignora erros de edição)."""
        try:
            await status_msg.edit_text(text, parse_mode="HTML")
        except Exception:
            pass  # mensagem pode já ter sido deletada ou inalterada

    # Verificar transação on-chain SEMPRE com preços atuais
    try:
        # SEMPRE usar force_refresh=True para garantir preços atualizados
        ok, msg_result, usd_paid, details = await resolve_payment_usd_autochain(
            tx_hash, force_refresh=True, progress_cb=_update_status
        )

        LOG.info(f"[PRICE-CHECK] Verificação com preços atuais - Hash: {tx_hash[:12]}... USD: ${float(usd_paid):.4f}" if usd_paid else f"[PRICE-CHECK] Falha na verificação - Hash: {tx_hash[:12]}...")
        
        # Apagar mensagem de progresso antes da resposta final
        with suppress(Exception):
            await status_msg.delete()

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
                            f"✅ Valor recebido: <b>${float(usd_paid):.2f} USD</b>\n"
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
                            f"✅ Valor recebido: <b>${float(usd_paid):.2f} USD</b>\n"
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
                        f"✅ Valor recebido: <b>${float(usd_paid):.2f} USD</b>\n"
                        f"👑 Plano ativado: <b>{plan_name} ({plan_days} dias)</b>\n"
                        f"📅 Válido até: <b>{vip_until.strftime('%d/%m/%Y')}</b>\n\n"
                        f"📬 Aguarde o convite do grupo VIP em breve!\n\n"
                        f"Obrigado! 🙏"
                    )

                return await msg.reply_text(welcome_msg, parse_mode="HTML")
            else:
                return await msg.reply_text(
                    f"❌ Valor pago (${float(usd_paid):.2f}) insuficiente para qualquer plano VIP."
                )
        else:
            return await msg.reply_text(f"❌ {msg_result}")

    except Exception as e:
        LOG.error(f"Erro ao verificar transação {tx_hash}: {e}")
        with suppress(Exception):
            await status_msg.delete()
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

    # Verificar se o UID é válido (veio via deep link com assinatura)
    # UIDs válidos: numéricos, < 15 dígitos, >= 100000000
    is_valid_uid = False
    if tg_id and str(tg_id).isdigit() and len(str(tg_id)) < 15 and int(tg_id) >= 100000000:
        is_valid_uid = True
        LOG.info(f"[INVITE-DEBUG] UID válido detectado: {tg_id} - ID real capturado via deep link")
    else:
        LOG.info(f"[INVITE-DEBUG] UID inválido ou ausente: '{tg_id}' - será tratado como temporário")

    is_temp_uid = not is_valid_uid
    actual_tg_id = int(tg_id) if is_valid_uid else None
    until = None
    link = None

    # Salvar pagamento com ID real (se disponível) ou temporário (user_id=0)
    with SessionLocal() as s:
        # Extrair informações do payment para salvar
        token_symbol = details.get("token_symbol", "Unknown")
        token_amount = details.get("amount_human", details.get("amount", "N/A"))

        # Usar ID real se disponível, senão usar 0 (temporário)
        user_id_to_save = actual_tg_id if actual_tg_id else 0

        if actual_tg_id:
            LOG.info(f"[PAYMENT-SAVE] Salvando pagamento com user_id={actual_tg_id} (ID real capturado via deep link)")
        else:
            LOG.info(f"[PAYMENT-SAVE] Salvando pagamento com user_id=0 (ID será capturado ao entrar no grupo)")

        p = Payment(
            tx_hash=tx_hash,
            user_id=user_id_to_save,
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

        # Se temos ID real, criar ou atualizar VipMembership usando vip_upsert_and_get_until
        # que já tem lógica de substituição correta
        if actual_tg_id:
            from utils import vip_upsert_and_get_until

            # vip_upsert_and_get_until é async, precisa ser chamado fora da sessão
            pass  # Será executado depois da sessão fechar
        else:
            # NÃO criar VipMembership aqui - será criado quando usuário entrar no grupo
            # Isso evita violação de constraint UNIQUE em user_id quando há múltiplos pagamentos pendentes
            LOG.info(f"[PAYMENT-SAVE] VIP será criado quando usuário entrar no grupo")

    # Criar/atualizar VIP membership se temos ID real (fora da sessão de Payment)
    if actual_tg_id:
        from utils import vip_upsert_and_get_until
        until = await vip_upsert_and_get_until(actual_tg_id, username, days)
        LOG.info(f"[VIP-UPSERT] VIP criado/atualizado para {actual_tg_id}: válido até {until.strftime('%d/%m/%Y %H:%M')}")
    else:
        # Para IDs temporários, calcular data estimada
        import datetime as dt
        until = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=days)

    LOG.info(f"[INVITE-DEBUG] Finalizando: is_temp_uid={is_temp_uid}, link={link is not None if link else False}")

    # Calcular data de expiração do VIP para mensagens (sempre usar 'until' se disponível)
    import datetime as dt
    vip_until = until if until else (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=days))
    vip_until_str = vip_until.strftime('%d/%m/%Y')

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
        # ID real capturado via deep link - enviar tudo no privado
        if bot_available and application and application.bot:
            try:
                from utils import create_invite_link_flexible
                link = await create_invite_link_flexible(application.bot, GROUP_VIP_ID, retries=3)
                LOG.info(f"[INVITE-REAL-ID] Convite gerado para ID real {actual_tg_id}: {link is not None}")

                # Salvar mapeamento link -> user_id para validação posterior
                if link:
                    import hashlib
                    link_hash = hashlib.md5(link.encode()).hexdigest()[:12]
                    from main import cfg_set
                    # Salvar (será limpo manualmente depois ou permanecerá como histórico)
                    cfg_set(f"invite_link_{link_hash}", str(actual_tg_id))
                    LOG.info(f"[LINK-PROTECTION] Link protegido para user {actual_tg_id}: {link_hash}")

                # Criar comprovante completo
                from main import now_utc
                comprovante = (
                    f"📜 <b>COMPROVANTE DE PAGAMENTO VIP</b> 📜\n"
                    f"{'='*35}\n\n"

                    f"📅 <b>Data:</b> {now_utc().strftime('%d/%m/%Y às %H:%M')}\n"
                    f"👤 <b>Usuário:</b> {username or 'N/A'}\n"
                    f"🆔 <b>ID Telegram:</b> <code>{actual_tg_id}</code>\n\n"

                    f"💰 <b>DETALHES DO PAGAMENTO</b>\n"
                    f"• <b>Valor Pago:</b> ${float(usd):.2f} USD\n"
                    f"• <b>Criptomoeda:</b> {token_symbol}\n"
                    f"• <b>Quantidade:</b> {token_amount}\n"
                    f"• <b>Hash:</b> <code>{tx_hash[:16]}...{tx_hash[-8:]}</code>\n\n"

                    f"👑 <b>VIP ATIVADO</b>\n"
                    f"• <b>Plano:</b> {plan_name}\n"
                    f"• <b>Duração:</b> {days} dias\n"
                    f"• <b>Válido até:</b> {until.strftime('%d/%m/%Y às %H:%M')}\n"
                    f"• <b>Status:</b> ✅ Ativo\n\n"
                )

                if link:
                    comprovante += (
                        f"🔗 <b>CONVITE DO GRUPO VIP</b>\n"
                        f"{link}\n\n"
                        f"⚠️ <b>IMPORTANTE:</b> Este link expira em 2 horas e tem apenas 1 uso.\n\n"
                        f"📁 <b>REGRAS DO GRUPO VIP</b>\n"
                        f"• Respeite todos os membros\n"
                        f"• Proibido spam ou conteúdo inapropriado\n"
                        f"• Não compartilhe links de convite\n"
                        f"• Mantenha conversa relevante ao tema\n"
                        f"• Proibido revenda de conteúdo\n\n"
                        f"🎉 <b>Bem-vindo ao grupo VIP!</b>\n"
                        f"Aproveite o conteúdo exclusivo!"
                    )
                else:
                    comprovante += (
                        f"⚠️ Não foi possível gerar o link de convite automaticamente.\n"
                        f"Entre em contato com o suporte para receber o convite.\n\n"
                        f"🎁 Seu VIP está ativo e válido!"
                    )

                # Enviar comprovante no privado
                await application.bot.send_message(
                    chat_id=actual_tg_id,
                    text=comprovante,
                    parse_mode="HTML"
                )
                LOG.info(f"[NOTIFY] ✅ Comprovante enviado no privado para {actual_tg_id}")

                # Enviar log para grupo de logs
                try:
                    from main import LOGS_GROUP_ID
                    log_msg = (
                        f"✅ <b>PAGAMENTO CONFIRMADO VIA DEEP LINK</b>\n"
                        f"👤 User: <code>{actual_tg_id}</code> (@{username or 'sem_username'})\n"
                        f"💰 Valor: ${float(usd):.2f} USD\n"
                        f"📅 Plano: {plan_name} ({days} dias)\n"
                        f"⏰ VIP até: {until.strftime('%d/%m/%Y %H:%M')}\n"
                        f"🔗 Link enviado: {'Sim' if link else 'Não'}\n"
                        f"📨 Comprovante enviado no privado"
                    )
                    await application.bot.send_message(
                        chat_id=LOGS_GROUP_ID,
                        text=log_msg,
                        parse_mode="HTML"
                    )
                    LOG.info(f"[NOTIFY] ✅ Log enviado para grupo de logs")
                except Exception as log_error:
                    LOG.warning(f"[NOTIFY] Erro ao enviar log: {log_error}")

            except Exception as e:
                LOG.error(f"[NOTIFY] Erro ao enviar comprovante: {e}")

        # Mensagem de retorno para a página web (com redirecionamento se houver link)
        if link:
            msg = (
                f"🎉 <b>PAGAMENTO CONFIRMADO!</b>\n\n"
                f"✅ Valor recebido: <b>${float(usd):.2f} USD</b>\n"
                f"👑 Plano ativado: <b>{plan_name} ({days} dias)</b>\n"
                f"📅 Válido até: <b>{vip_until_str}</b>\n\n"
                f"📬 <b>Redirecionando para o grupo VIP...</b>\n"
                f"Verifique também suas mensagens no Telegram!\n\n"
                f"🎁 Aproveite o conteúdo exclusivo!"
            )
            # Incluir link no payload para redirecionar automaticamente
            return True, msg, {"invite": link, "usd": usd, "days": days, "private_sent": True}
        else:
            msg = (
                f"🎉 <b>PAGAMENTO CONFIRMADO!</b>\n\n"
                f"✅ Valor recebido: <b>${float(usd):.2f} USD</b>\n"
                f"👑 Plano ativado: <b>{plan_name} ({days} dias)</b>\n"
                f"📅 Válido até: <b>{vip_until_str}</b>\n\n"
                f"📬 <b>Verifique suas mensagens no Telegram!</b>\n"
                f"Enviamos o comprovante no seu privado.\n\n"
                f"🎁 Entre em contato para receber o convite do grupo!"
            )
            return True, msg, {"usd": usd, "days": days, "private_sent": True}

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
