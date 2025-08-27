from dotenv import load_dotenv
import os
from typing import Dict, List

load_dotenv()


def _load_default_chain() -> Dict[str, str]:
    """Fallback to legacy single-chain env variables."""
    return {
        "chain_name": os.getenv("CHAIN_NAME", "Polygon").strip(),
        "rpc_url": os.getenv("RPC_URL", "").strip(),
        "wallet_address": (os.getenv("WALLET_ADDRESS", "").strip() or "").lower(),
        "token_contract": (os.getenv("TOKEN_CONTRACT", "").strip().lower() or None),
        "decimals": int(os.getenv("TOKEN_DECIMALS", "18")),
    }


def _load_chain(prefix: str) -> Dict[str, str]:
    """Load chain configuration using a prefix (e.g., 'POLYGON')."""
    upp = prefix.upper()
    return {
        "chain_name": os.getenv(f"{upp}_CHAIN_NAME", prefix).strip(),
        "rpc_url": os.getenv(f"{upp}_RPC_URL", "").strip(),
        "wallet_address": (os.getenv(f"{upp}_WALLET_ADDRESS", "").strip() or "").lower(),
        "token_contract": (os.getenv(f"{upp}_TOKEN_CONTRACT", "").strip().lower() or None),
        "decimals": int(os.getenv(f"{upp}_TOKEN_DECIMALS", "18")),
    }


_chain_names = os.getenv("CHAINS")
if _chain_names:
    names = [n.strip() for n in _chain_names.split(",") if n.strip()]
    CHAIN_CONFIGS: List[Dict[str, str]] = [_load_chain(name) for name in names]
else:
    CHAIN_CONFIGS = [_load_default_chain()]