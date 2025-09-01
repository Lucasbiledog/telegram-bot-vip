"""Integração com Web3Auth para fornecer providers Web3."""
from typing import Optional
import os
from web3 import Web3

try:  # pragma: no cover - best effort, dependência externa
    from web3auth import Web3Auth  # type: ignore
except Exception:  # ImportError ou qualquer falha de runtime
    Web3Auth = None  # type: ignore

INFURA_PROJECT_ID = os.getenv("INFURA_PROJECT_ID", "").strip()
INFURA_RPC = {
    "ethereum": f"https://mainnet.infura.io/v3/{INFURA_PROJECT_ID}",
    "polygon": f"https://polygon-mainnet.infura.io/v3/{INFURA_PROJECT_ID}",
    "arbitrum": f"https://arbitrum-mainnet.infura.io/v3/{INFURA_PROJECT_ID}",
    "optimism": f"https://optimism-mainnet.infura.io/v3/{INFURA_PROJECT_ID}",
    "base": f"https://base-mainnet.infura.io/v3/{INFURA_PROJECT_ID}",
    "bsc": os.getenv("RPC_BSC", "").strip(),
}


def get_provider(chain: str, rpc_url: str = "") -> Optional[Web3.HTTPProvider]:
    """Retorna um HTTPProvider autenticado via Web3Auth para a rede indicada.

    ``rpc_url`` pode ser usado para sobrescrever a URL padrão (por exemplo,
    quando derivada dinamicamente de ``config.CHAIN_CONFIGS``). Se o Web3Auth
    não estiver configurado ou ocorrer algum erro, retorna um provider HTTP
    simples apontando para a URL configurada.
    """
    url = (rpc_url or INFURA_RPC.get(chain) or "").strip()
    if not url:
        return None

    if Web3Auth:
        try:
            client_id = os.getenv("WEB3AUTH_CLIENT_ID", "").strip()
            private_key = os.getenv("WEB3AUTH_PRIVATE_KEY", "").strip()
            if client_id and private_key:
                sdk = Web3Auth(client_id=client_id, private_key=private_key, rpc_target=url)
                url = getattr(sdk, "get_provider", lambda: url)()
        except Exception:
            pass

    return Web3.HTTPProvider(url, request_kwargs={"timeout": 20})
