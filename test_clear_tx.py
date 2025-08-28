import os
os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
import time
import asyncio
import importlib
from types import SimpleNamespace
import pytest
from sqlalchemy.orm import Session
import sys, pathlib
@pytest.fixture
def main_module(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "123:ABC")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("AUTO_APPROVE_CRYPTO", "0")
    monkeypatch.setenv("WEBHOOK_URL", "http://localhost")
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
    import main
    importlib.reload(main)
    main.Base.metadata.drop_all(bind=main.engine)
    main.Base.metadata.create_all(bind=main.engine)
    return main
def _patch_slow_commit(monkeypatch):
    orig_commit = Session.commit
    def slow_commit(self, *args, **kwargs):
        time.sleep(0.2)
        return orig_commit(self, *args, **kwargs)
    monkeypatch.setattr(Session, "commit", slow_commit, raising=False)
def test_tx_cmd_concurrent(main_module, monkeypatch):
    main = main_module
    _patch_slow_commit(monkeypatch)
    async def fake_verify_tx_any(tx_hash):
        return {"ok": False, "reason": "Transação não encontrada", "chain_name": "eth", "amount_usd": 10}
    monkeypatch.setattr(main, "verify_tx_any", fake_verify_tx_any)
    monkeypatch.setattr(main, "list_admin_ids", lambda: [])
    monkeypatch.setattr(main, "schedule_pending_tx_recheck", lambda: None)
    async def fake_dm(*args, **kwargs):
        pass
    monkeypatch.setattr(main, "dm", fake_dm)
    class DummyMsg:
        async def reply_text(self, *args, **kwargs):
            pass
    class DummyUser:
        def __init__(self, uid):
            self.id = uid
            self.username = f"user{uid}"
    async def call(hash_val, uid):
        update = SimpleNamespace(effective_message=DummyMsg(), effective_user=DummyUser(uid))
        context = SimpleNamespace(args=[hash_val])
        await main.tx_cmd(update, context)
    h1 = "0x" + "1" * 64
    h2 = "0x" + "2" * 64
    async def runner():
        await asyncio.gather(call(h1, 1), call(h2, 2))
    start = time.perf_counter()
    asyncio.run(runner())
    elapsed = time.perf_counter() - start
    assert elapsed < 0.35
def test_clear_tx_cmd_concurrent(main_module, monkeypatch):
    main = main_module
    monkeypatch.setattr(main, "is_admin", lambda uid: True)
    class DummyQuery:
        def filter(self, *args, **kwargs):
            return self
        def all(self):
            return [1]
        def delete(self, *args, **kwargs):
            pass
        def update(self, *args, **kwargs):
            pass
    class DummySession:
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            pass
        def query(self, *args, **kwargs):
            return DummyQuery()
        def commit(self):
            time.sleep(0.2)
        def rollback(self):
            pass
    monkeypatch.setattr(main, "SessionLocal", lambda: DummySession())
    hashes = ["0x" + "3" * 64, "0x" + "4" * 64]
    class DummyMsg:
        async def reply_text(self, *args, **kwargs):
            pass
    class DummyUser:
        id = 1
        username = "admin"
    async def call(h):
        update = SimpleNamespace(effective_message=DummyMsg(), effective_user=DummyUser())
        context = SimpleNamespace(args=[h])
        await main.clear_tx_cmd(update, context)
    async def runner():
        await asyncio.gather(call(hashes[0]), call(hashes[1]))
    start = time.perf_counter()
    asyncio.run(runner())
    elapsed = time.perf_counter() - start
    assert elapsed < 0.35