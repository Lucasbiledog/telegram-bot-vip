import asyncio
import importlib
import sys
from pathlib import Path
from unittest.mock import AsyncMock


def test_clear_tx_allows_resubmission(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("AUTO_APPROVE_CRYPTO", "0")
    monkeypatch.setenv("BOT_TOKEN", "test")
    monkeypatch.setenv("WEBHOOK_URL", "http://example.com")
    monkeypatch.setenv("SELF_URL", "http://example.com")
    sys.path.append(str(Path(__file__).resolve().parents[1]))
    main = importlib.reload(importlib.import_module("main"))

    monkeypatch.setattr(main, "is_admin", lambda uid: True)
    monkeypatch.setattr(
        main,
        "verify_tx_any",
        AsyncMock(return_value={"ok": False, "reason": "Aguardando", "chain_name": "ETH"}),
    )
    monkeypatch.setattr(main, "dm", AsyncMock())
    monkeypatch.setattr(main, "list_admin_ids", lambda: [])

    tx = "0x" + "a" * 64

    class DummyUser:
        def __init__(self, uid, username="user"):
            self.id = uid
            self.username = username

    class DummyMessage:
        def __init__(self):
            self.texts = []

        async def reply_text(self, text, *args, **kwargs):
            self.texts.append(text)

    class DummyUpdate:
        def __init__(self, user):
            self.effective_user = user
            self.effective_message = DummyMessage()

    class DummyContext:
        def __init__(self, args):
            self.args = args

    asyncio.run(main.tx_cmd(DummyUpdate(DummyUser(1, "alice")), DummyContext([tx])))
    with main.SessionLocal() as s:
        assert s.query(main.Payment).filter(main.Payment.tx_hash == tx).count() == 1

    asyncio.run(main.clear_tx_cmd(DummyUpdate(DummyUser(2, "admin")), DummyContext([tx])))
    with main.SessionLocal() as s:
        assert s.query(main.Payment).filter(main.Payment.tx_hash == tx).count() == 0

    asyncio.run(main.tx_cmd(DummyUpdate(DummyUser(1, "alice")), DummyContext([tx])))
    with main.SessionLocal() as s:
        payments = s.query(main.Payment).filter(main.Payment.tx_hash == tx).all()
        assert len(payments) == 1
        assert payments[0].user_id == 1