import os

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional dependency
    def load_dotenv() -> None:  # type: ignore
        return None

load_dotenv()

SELF_URL = os.getenv("SELF_URL", "")
WEBAPP_URL = os.getenv("WEBAPP_URL") or f"{SELF_URL.rstrip('/')}/pay/"

__all__ = ["SELF_URL", "WEBAPP_URL"]