# evm_pay.py
import os, json, time, math
from typing import Optional, Dict, Any, List, Tuple
import requests
from web3 import Web3
from web3.types import TxReceipt
from web3auth_provider import get_provider
from config import CHAIN_CONFIGS
from web3auth_chains import load_web3auth_chains

RECEIVING_ADDRESS = Web3.to_checksum_address(
    os.getenv("RECEIVING_ADDRESS", "0x0000000000000000000000000000000000000000")
)

# Redes EVM suportadas são derivadas automaticamente de ``config.CHAIN_CONFIGS``.
# Esse objeto lê a variável de ambiente ``CHAINS`` e fornece ``chain_name`` e
# ``rpc_url`` para cada rede. Se ``CHAIN_CONFIGS`` estiver vazio, caímos no
# conjunto oficial fornecido por ``web3auth_chains.load_web3auth_chains``.
# As URLs podem ser sobrescritas via variáveis ``<CHAIN>_RPC_URL``.
def _discover_supported() -> Dict[str, str]:
    chains: Dict[str, str] = {}
    for cfg in CHAIN_CONFIGS:
        name = (cfg.get("chain_name") or "").strip().lower()
        rpc = (cfg.get("rpc_url") or "").strip()
        if name and rpc:
            chains[name] = rpc
    if not chains:
        try:
            data = load_web3auth_chains()
            for name, info in data.items():
                rpc = (info.get("rpc") or "").strip()
                if name and rpc:
                    chains[name.strip().lower()] = rpc
        except Exception:
            pass
    return chains

SUPPORTED: Dict[str, str] = _discover_supported()

# topic do evento ERC20 Transfer(address,address,uint256)
ERC20_TRANSFER_TOPIC = Web3.keccak(text="Transfer(address,address,uint256)").hex()

# ABI mínimo
ERC20_ABI = [
    {"constant":True,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"type":"function"},
    {"constant":True,"inputs":[],"name":"symbol","outputs":[{"name":"","type":"string"}],"type":"function"}
]

def get_web3(chain: str, rpc_url: str = "") -> Optional[Web3]:
    provider = get_provider(chain, rpc_url)
    if not provider:
        return None
    return Web3(provider)

def _native_symbol(chain: str) -> str:
    return {
        "ethereum":"ETH",
        "polygon":"MATIC",
        "arbitrum":"ETH",
        "optimism":"ETH",
        "base":"ETH",
        "bsc":"BNB",
    }.get(chain, "ETH")

def _coingecko_native_id(chain: str) -> str:
    return {
        "ethereum":"ethereum",
        "polygon":"polygon-pos",
        "arbitrum":"arbitrum",
        "optimism":"optimism",
        "base":"base",
        "bsc":"binancecoin",
    }.get(chain, "ethereum")

def _coingecko_token_price(chain: str, token_address: str) -> Optional[float]:
    # preços ERC20: /simple/token_price/{platform}?contract_addresses=...&vs_currencies=usd
    platform = {
        "ethereum":"ethereum",
        "polygon":"polygon-pos",
        "arbitrum":"arbitrum-one",
        "optimism":"optimistic-ethereum",
        "base":"base",
        "bsc":"binance-smart-chain",
    }.get(chain)
    if not platform:
        return None
    try:
        r = requests.get(
            f"https://api.coingecko.com/api/v3/simple/token_price/{platform}",
            params={"contract_addresses": token_address, "vs_currencies":"usd"},
            timeout=15
        )
        data = r.json()
        key = token_address.lower()
        if key in data and "usd" in data[key]:
            return float(data[key]["usd"])
    except Exception:
        pass
    return None

def _coingecko_native_price(chain: str) -> Optional[float]:
    cid = _coingecko_native_id(chain)
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": cid, "vs_currencies":"usd"},
            timeout=15
        )
        data = r.json()
        if cid in data and "usd" in data[cid]:
            return float(data[cid]["usd"])
    except Exception:
        pass
    return None

def _decode_erc20_value(amount_wei: int, decimals: int) -> float:
    return amount_wei / float(10 ** decimals)

def _try_token_metadata(w3: Web3, token_addr: str) -> Tuple[str,int]:
    try:
        c = w3.eth.contract(address=Web3.to_checksum_address(token_addr), abi=ERC20_ABI)
        sym = c.functions.symbol().call()
        dec = c.functions.decimals().call()
        return sym, int(dec)
    except Exception:
        return "TKN", 18

def _sum_transferred_to_me(w3: Web3, receipt: TxReceipt) -> List[Dict[str, Any]]:
    """Varre logs ERC20 Transfer para meu endereço e devolve lista de {token, amount, symbol, decimals}."""
    results = []
    for lg in receipt["logs"]:
        if lg["topics"] and lg["topics"][0].hex().lower() == ERC20_TRANSFER_TOPIC.lower():
            # topics[1] from, topics[2] to — ambos bytes32; tail 20 bytes é o address
            if len(lg["topics"]) >= 3:
                to_raw = lg["topics"][2].hex()  # 0x + 64 hex
                to_addr = "0x" + to_raw[-40:]
                if Web3.to_checksum_address(to_addr) == RECEIVING_ADDRESS:
                    token = lg["address"]
                    # value vem nos dados (uint256)
                    amount_wei = int(lg["data"], 16)
                    sym, dec = _try_token_metadata(w3, token)
                    amount = _decode_erc20_value(amount_wei, dec)
                    results.append({"token": Web3.to_checksum_address(token), "amount": amount, "decimals": dec, "symbol": sym})
    return results

def fetch_tx_on_chain(chain: str, tx_hash: str) -> Optional[Dict[str, Any]]:
    w3 = get_web3(chain)
    if not w3:
        return None
    try:
        tx = w3.eth.get_transaction(tx_hash)
    except Exception:
        return None

    # Precisamos do receipt pra logs/confirmations
    try:
        receipt = w3.eth.get_transaction_receipt(tx_hash)
    except Exception:
        return None

    # Confirmações (melhor esforço)
    latest = w3.eth.block_number
    confirmations = latest - receipt["blockNumber"] + 1 if receipt and receipt.get("blockNumber") else 0

    details = {
        "chain": chain,
        "hash": tx_hash,
        "block": receipt.get("blockNumber"),
        "confirmations": confirmations,
        "found": False,
        "native": 0.0,
        "native_symbol": _native_symbol(chain),
        "erc20": [],     # lista de {token, amount, symbol, decimals, usd_price?, usd_amount?}
        "usd_total": 0.0
    }

    # 1) Nativo: se o TO == RECEIVING_ADDRESS
    try:
        if tx.get("to") and Web3.to_checksum_address(tx["to"]) == RECEIVING_ADDRESS:
            val_native = float(w3.from_wei(tx["value"], "ether"))  # Ether-like unidade
            details["native"] = val_native
    except Exception:
        pass

    # 2) Tokens ERC20 (Transfer para meu endereço)
    try:
        tokens = _sum_transferred_to_me(w3, receipt)
        details["erc20"] = tokens
    except Exception:
        pass

    if details["native"] > 0 or len(details["erc20"]) > 0:
        details["found"] = True

    # Preço em USD
    usd_total = 0.0
    if details["native"] > 0:
        p = _coingecko_native_price(chain)
        if p:
            usd_total += details["native"] * p

    for t in details["erc20"]:
        p = _coingecko_token_price(chain, t["token"]) or 0.0
        t["usd_price"] = p
        t["usd_amount"] = t["amount"] * p
        usd_total += t["usd_amount"]

    details["usd_total"] = usd_total
    return details

def find_tx_any_chain(tx_hash: str) -> Optional[Dict[str, Any]]:
    tx_hash = tx_hash.strip().lower()
    if not tx_hash.startswith("0x") or len(tx_hash) != 66:
        # tentar normalizar quando o usuário manda sem 0x
        if len(tx_hash) == 64:
            tx_hash = "0x" + tx_hash
        else:
            return None
    for chain in SUPPORTED:
        info = fetch_tx_on_chain(chain, tx_hash)
        if info and info.get("found"):
            return info
    return None

def pick_tier(amount_usd: float) -> Optional[str]:
    tiers = json.loads(os.getenv("VIP_TIERS_JSON", '{"basic":10,"pro":25,"ultra":50}'))
    # ordena por valor asc
    by_min = sorted(tiers.items(), key=lambda kv: kv[1])
    chosen = None
    for name, min_usd in by_min:
        if amount_usd >= float(min_usd):
            chosen = name
    return chosen
