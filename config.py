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


__all__ = ["SELF_URL", "WEBAPP_URL", "ADMIN_IDS", "OWNER_ID", "TELEGRAM_API_ID", "TELEGRAM_API_HASH", "SOURCE_CHAT_ID"]