from dotenv import load_dotenv
import os
from typing import Dict, List, Any

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


def _load_default_chain() -> Dict[str, Any]:
    """Fallback to legacy single-chain env variables."""
    chain_name = os.getenv("CHAIN_NAME", "Polygon").strip()
    chain_id_env = os.getenv("CHAIN_ID")
    chain_id = int(chain_id_env) if chain_id_env else CHAIN_ID_MAP.get(chain_name, 0)
    return {
        "chain_name": chain_name,
        "chain_id": chain_id,
        "rpc_url": os.getenv("RPC_URL", "").strip(),
        "wallet_address": (os.getenv("WALLET_ADDRESS", "").strip() or "").lower(),
        "token_contract": (os.getenv("TOKEN_CONTRACT", "").strip().lower() or None),
        "decimals": int(os.getenv("TOKEN_DECIMALS", "18")),
        "bscscan_api_key": os.getenv("BSCSCAN_API_KEY", "").strip(),
        "etherscan_api_key": os.getenv("ETHERSCAN_API_KEY", "").strip(),
    }


def _load_chain(prefix: str) -> Dict[str, Any]:
    """Load chain configuration using a prefix (e.g., 'POLYGON')."""
    upp = prefix.upper()
    chain_name = os.getenv(f"{upp}_CHAIN_NAME", prefix).strip()
    chain_id_env = os.getenv(f"{upp}_CHAIN_ID")
    chain_id = int(chain_id_env) if chain_id_env else CHAIN_ID_MAP.get(chain_name, 0)
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
    }


_chain_names = os.getenv("CHAINS")
if _chain_names:
    names = [n.strip() for n in _chain_names.split(",") if n.strip()]
    CHAIN_CONFIGS: List[Dict[str, Any]] = [_load_chain(name) for name in names]
else:
    CHAIN_CONFIGS = [_load_default_chain()]
