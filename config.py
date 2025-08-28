import os
from typing import Dict, List, Any
from dotenv import load_dotenv

load_dotenv()


CHAIN_ID_MAP = {
    "Ethereum": 1,
    "Goerli": 5,
    "Sepolia": 11155111,
    "Binance Smart Chain": 56,
    "Polygon": 137,
    "Avalanche": 43114,
    "Fantom": 250,
    "Arbitrum": 42161,
    "Optimism": 10,
    "Base": 8453,
}

# Mapping to infer the native token symbol for common chains. This is used as a
# sensible default when the environment does not explicitly define one.
CHAIN_SYMBOL_MAP = {
    "Ethereum": "ETH",
    "Goerli": "ETH",
    "Sepolia": "ETH",
    "Binance Smart Chain": "BNB",
    "Polygon": "MATIC",
    "Avalanche": "AVAX",
    "Fantom": "FTM",
    # Rollups and L2s currently use ETH as their native token
    "Arbitrum": "ETH",
    "Optimism": "ETH",
    "Base": "ETH",
}


def _load_default_chain() -> Dict[str, Any]:
    """Fallback to legacy single-chain env variables."""
    chain_name = os.getenv("CHAIN_NAME", "Polygon").strip()
    chain_id_env = os.getenv("CHAIN_ID")
    chain_id = int(chain_id_env) if chain_id_env else CHAIN_ID_MAP.get(chain_name, 0)
    symbol = os.getenv("SYMBOL") or CHAIN_SYMBOL_MAP.get(chain_name, "")
    return {
        "chain_name": chain_name,
        "chain_id": chain_id,
        "rpc_url": os.getenv("RPC_URL", "").strip(),
        "wallet_address": (os.getenv("WALLET_ADDRESS", "").strip() or "").lower(),
        "token_contract": (os.getenv("TOKEN_CONTRACT", "").strip().lower() or None),
        "decimals": int(os.getenv("TOKEN_DECIMALS", "18")),
        "bscscan_api_key": os.getenv("BSCSCAN_API_KEY", "").strip(),
        "etherscan_api_key": os.getenv("ETHERSCAN_API_KEY", "").strip(),
        "symbol": symbol.strip(),
    }


def _load_chain(prefix: str) -> Dict[str, Any]:
    """Load chain configuration using a prefix (e.g., 'POLYGON')."""
    upp = prefix.upper()
    chain_name = os.getenv(f"{upp}_CHAIN_NAME", prefix).strip()
    chain_id_env = os.getenv(f"{upp}_CHAIN_ID")
    chain_id = int(chain_id_env) if chain_id_env else CHAIN_ID_MAP.get(chain_name, 0)
    symbol = os.getenv(f"{upp}_SYMBOL") or CHAIN_SYMBOL_MAP.get(chain_name, "")
    return {
        "chain_name": chain_name,
        "chain_id": chain_id,
        "rpc_url": os.getenv(f"{upp}_RPC_URL", "").strip(),
        "wallet_address": (os.getenv(f"{upp}_WALLET_ADDRESS", "").strip() or "").lower(),
        "token_contract": (os.getenv(f"{upp}_TOKEN_CONTRACT", "").strip().lower() or None),
        "decimals": int(os.getenv(f"{upp}_TOKEN_DECIMALS", "18")),
        "bscscan_api_key": os.getenv(
            f"{upp}_BSCSCAN_API_KEY", os.getenv("BSCSCAN_API_KEY", "")
        ).strip(),
        "etherscan_api_key": os.getenv(
            f"{upp}_ETHERSCAN_API_KEY", os.getenv("ETHERSCAN_API_KEY", "")
        ).strip(),
        "symbol": symbol.strip(),
    }


_chain_names = os.getenv("CHAINS")
if _chain_names:
    names = [n.strip() for n in _chain_names.split(",") if n.strip()]
    CHAIN_CONFIGS: List[Dict[str, Any]] = [_load_chain(name) for name in names]
else:
    CHAIN_CONFIGS = [_load_default_chain()]
