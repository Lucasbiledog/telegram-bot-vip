import os

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional dependency
    def load_dotenv() -> None:  # type: ignore
        return None

load_dotenv()

SELF_URL = os.getenv("SELF_URL", "")
WEBAPP_URL = os.getenv("WEBAPP_URL") or f"{SELF_URL.rstrip('/')}/pay/"
OWNER_ID = int(os.getenv("OWNER_ID", "8520246396"))
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]

# Telegram API credentials (for Pyrogram indexer)
TELEGRAM_API_ID = os.getenv("TELEGRAM_API_ID", "")
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "")

# Source chat for file indexing
SOURCE_CHAT_ID = int(os.getenv("SOURCE_CHAT_ID", "-1003080645605"))


# ==========================================================
# PREÇOS DOS PLANOS VIP (ALTERE SOMENTE AQUI)
# ==========================================================
# Para modo teste use: 1.0, 2.0, 3.0, 4.0
# Para produção use:  30.0, 70.0, 110.0, 179.0
VIP_PRICE_MENSAL     = float(os.getenv("VIP_PRICE_MENSAL",     "1.0"))
VIP_PRICE_TRIMESTRAL = float(os.getenv("VIP_PRICE_TRIMESTRAL", "2.0"))
VIP_PRICE_SEMESTRAL  = float(os.getenv("VIP_PRICE_SEMESTRAL",  "3.0"))
VIP_PRICE_ANUAL      = float(os.getenv("VIP_PRICE_ANUAL",      "4.0"))

# Dicionário pronto para uso em todo o projeto
VIP_PRICES = {
    30:  VIP_PRICE_MENSAL,
    90:  VIP_PRICE_TRIMESTRAL,
    180: VIP_PRICE_SEMESTRAL,
    365: VIP_PRICE_ANUAL,
}

def vip_plans_text() -> str:
    """Retorna texto formatado dos planos para mensagens do bot."""
    return (
        f"• Mensal (30 dias): ${VIP_PRICE_MENSAL:.2f}\n"
        f"• Trimestral (90 dias): ${VIP_PRICE_TRIMESTRAL:.2f}\n"
        f"• Semestral (180 dias): ${VIP_PRICE_SEMESTRAL:.2f}\n"
        f"• Anual (365 dias): ${VIP_PRICE_ANUAL:.2f}"
    )

def vip_plans_text_usd() -> str:
    """Retorna texto formatado com ' USD' no final de cada linha."""
    return (
        f"• 30 dias: ${VIP_PRICE_MENSAL:.2f} USD (Mensal)\n"
        f"• 90 dias: ${VIP_PRICE_TRIMESTRAL:.2f} USD (Trimestral)\n"
        f"• 180 dias: ${VIP_PRICE_SEMESTRAL:.2f} USD (Semestral)\n"
        f"• 365 dias: ${VIP_PRICE_ANUAL:.2f} USD (Anual)"
    )

__all__ = [
    "SELF_URL", "WEBAPP_URL", "ADMIN_IDS", "OWNER_ID",
    "TELEGRAM_API_ID", "TELEGRAM_API_HASH", "SOURCE_CHAT_ID",
    "VIP_PRICE_MENSAL", "VIP_PRICE_TRIMESTRAL", "VIP_PRICE_SEMESTRAL", "VIP_PRICE_ANUAL",
    "VIP_PRICES", "vip_plans_text", "vip_plans_text_usd",
]