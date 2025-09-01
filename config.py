import os
from typing import Dict, List, Any
from dotenv import load_dotenv

from web3auth_chains import load_web3auth_chains

load_dotenv()


try:
    _CHAIN_DATA = load_web3auth_chains()
except Exception:
    # Fallback to a minimal built-in dataset when the official list is
    # unreachable. RPC URLs can still be overridden via environment
    # variables.
    _CHAIN_DATA = {
        "Ethereum": {"chain_id": 1, "symbol": "ETH", "rpc": "https://rpc.ankr.com/eth"},
        "Goerli": {"chain_id": 5, "symbol": "ETH", "rpc": "https://rpc.ankr.com/eth_goerli"},
        "Sepolia": {"chain_id": 11155111, "symbol": "ETH", "rpc": "https://rpc.sepolia.org"},
        "Binance Smart Chain": {"chain_id": 56, "symbol": "BNB", "rpc": "https://bsc-dataseed.binance.org"},
        "Polygon": {"chain_id": 137, "symbol": "MATIC", "rpc": "https://polygon-rpc.com"},
        "Avalanche": {"chain_id": 43114, "symbol": "AVAX", "rpc": "https://api.avax.network/ext/bc/C/rpc"},
        "Fantom": {"chain_id": 250, "symbol": "FTM", "rpc": "https://rpc.ftm.tools"},
        "Arbitrum": {"chain_id": 42161, "symbol": "ETH", "rpc": "https://arb1.arbitrum.io/rpc"},
        "Optimism": {"chain_id": 10, "symbol": "ETH", "rpc": "https://mainnet.optimism.io"},
        "Base": {"chain_id": 8453, "symbol": "ETH", "rpc": "https://mainnet.base.org"},
    }


def _load_default_chain() -> Dict[str, Any]:
    """Fallback to legacy single-chain env variables."""
    chain_name = os.getenv("CHAIN_NAME", "Polygon").strip()
    info = _CHAIN_DATA.get(chain_name, {})
    chain_id_env = os.getenv("CHAIN_ID")
    chain_id = int(chain_id_env) if chain_id_env else info.get("chain_id", 0)
    symbol = os.getenv("SYMBOL") or info.get("symbol", "")
    return {
        "chain_name": chain_name,
        "chain_id": chain_id,
        "rpc_url": os.getenv("RPC_URL", info.get("rpc", "")).strip(),
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
    info = _CHAIN_DATA.get(chain_name, {})
    chain_id_env = os.getenv(f"{upp}_CHAIN_ID")
    chain_id = int(chain_id_env) if chain_id_env else info.get("chain_id", 0)
    symbol = os.getenv(f"{upp}_SYMBOL") or info.get("symbol", "")
    return {
        "chain_name": chain_name,
        "chain_id": chain_id,
        "rpc_url": os.getenv(f"{upp}_RPC_URL", info.get("rpc", "")).strip(),
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
