from __future__ import annotations
import os, logging
from typing import Optional
import httpx
from web3 import Web3

LOG = logging.getLogger("payments")

WALLET_ADDRESS = os.getenv("WALLET_ADDRESS", "")
RPC_URL = os.getenv("RPC_URL", "")
MIN_CONFIRMATIONS = int(os.getenv("MIN_CONFIRMATIONS", "12"))
TOKEN_DECIMALS = int(os.getenv("TOKEN_DECIMALS", "18"))
COINGECKO_NATIVE_ID = os.getenv("COINGECKO_NATIVE_ID", "polygon-pos")
COINGECKO_PLATFORM = os.getenv("COINGECKO_PLATFORM", "polygon-pos")

w3 = Web3(Web3.HTTPProvider(RPC_URL)) if RPC_URL else None
ERC20_TRANSFER_SIG = Web3.keccak(text="Transfer(address,address,uint256)").hex()

def _topic_addr(topic_hex: str) -> str:
    return Web3.to_checksum_address("0x" + topic_hex[-40:])

async def _get_confirmations(block_number: int | None) -> int:
    if not w3 or not block_number:
        return 0
    latest = w3.eth.block_number
    return max(0, latest - block_number)

async def _cg_get_native_usd(coingecko_id: str) -> Optional[float]:
    if not coingecko_id: return None
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={coingecko_id}&vs_currencies=usd"
    try:
        async with httpx.AsyncClient(timeout=10) as cli:
            r = await cli.get(url)
            r.raise_for_status()
            data = r.json()
            return float(data.get(coingecko_id, {}).get("usd")) if data else None
    except Exception:
        LOG.warning("Falha price native id=%s", coingecko_id)
        return None

async def _cg_get_token_usd(platform: str, token_addr: str) -> Optional[float]:
    if not platform or not token_addr: return None
    token_addr = token_addr.lower()
    url = f"https://api.coingecko.com/api/v3/simple/token_price/{platform}?contract_addresses={token_addr}&vs_currencies=usd"
    try:
        async with httpx.AsyncClient(timeout=10) as cli:
            r = await cli.get(url)
            r.raise_for_status()
            data = r.json()
            # CoinGecko key can be checksum or lower-case
            for k, v in data.items():
                if k.lower() == token_addr and "usd" in v:
                    return float(v["usd"])
            return None
    except Exception:
        LOG.warning("Falha price token platform=%s token=%s", platform, token_addr)
        return None

def _from_wei(value: int, decimals: int = 18) -> float:
    return float(value) / float(10 ** decimals)

async def resolve_payment_usd(tx_hash: str) -> tuple[bool, str, Optional[float]]:
    """
    Lê on-chain e retorna (ok, mensagem, amount_usd) para pagamento nativo OU ERC-20.
    Regras:
    - se tx.to == WALLET_ADDRESS -> nativo; converte usando COINGECKO_NATIVE_ID
    - senão, procura logs Transfer(token) -> WALLET_ADDRESS; converte via COINGECKO_PLATFORM + token address
    - exige MIN_CONFIRMATIONS e receipt.status == 1
    """
    if not w3:
        return False, "RPC não configurado.", None

    try:
        tx = w3.eth.get_transaction(tx_hash)
        if not tx:
            return False, "Transação não encontrada.", None

        receipt = None
        if tx.get("blockHash"):
            receipt = w3.eth.get_transaction_receipt(tx_hash)

        confirmations = await _get_confirmations(tx.get("blockNumber"))
        if confirmations < MIN_CONFIRMATIONS:
            return False, f"Aguardando confirmações: {confirmations}/{MIN_CONFIRMATIONS}", None

        if receipt and receipt.get("status") != 1:
            return False, "Transação revertida.", None

        to_addr = (tx.get("to") or "").lower()
        if WALLET_ADDRESS and to_addr == WALLET_ADDRESS.lower():
            # Native
            amount_native = _from_wei(int(tx["value"]), 18)
            px = await _cg_get_native_usd(COINGECKO_NATIVE_ID)
            if not px:
                return False, "Preço USD indisponível (nativo).", None
            paid_usd = amount_native * px
            return True, f"OK nativo: {amount_native:.6f} * ${px:.4f} = ${paid_usd:.2f}", paid_usd

        # Token (ERC-20)
        if not receipt:
            return False, "Receipt indisponível para token.", None

        found_value_raw = None
        found_token_addr = None

        for log in receipt.get("logs", []):
            addr = (log.get("address") or "").lower()
            topics = log.get("topics") or []
            if len(topics) < 3: 
                continue
            t0 = topics[0].hex().lower() if hasattr(topics[0],'hex') else topics[0].lower()
            if t0 != ERC20_TRANSFER_SIG.lower():
                continue
            t2 = topics[2].hex() if hasattr(topics[2],'hex') else topics[2]
            toA = _topic_addr(t2)
            if WALLET_ADDRESS and toA.lower() != WALLET_ADDRESS.lower():
                continue
            try:
                data_hex = log.get("data")
                value_raw = int(data_hex, 16)
            except Exception:
                continue
            found_value_raw = value_raw
            found_token_addr = addr
            break

        if found_value_raw is None or not found_token_addr:
            return False, "Nenhuma transferência válida para a carteira destino.", None

        px_token = await _cg_get_token_usd(COINGECKO_PLATFORM, found_token_addr)
        if not px_token:
            return False, "Preço USD indisponível (token).", None

        token_amount = float(found_value_raw) / float(10 ** TOKEN_DECIMALS)
        paid_usd = token_amount * px_token
        return True, f"OK token: {token_amount:.6f} * ${px_token:.4f} = ${paid_usd:.2f}", paid_usd

    except Exception as e:
        LOG.exception("resolve_payment_usd erro")
        return False, f"Erro ao validar: {e}", None
