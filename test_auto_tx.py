import os
os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"

import importlib
import sys
import pathlib
import asyncio
from types import SimpleNamespace

import pytest


@pytest.fixture
def main_module(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "123:ABC")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("WEBHOOK_URL", "http://localhost")
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
    import main
    importlib.reload(main)
    main.Base.metadata.drop_all(bind=main.engine)
    main.Base.metadata.create_all(bind=main.engine)
    return main


def test_extract_tx_hashes(main_module):
    main = main_module
    h = "0x" + "a" * 64
    text = f"foo {h} bar"
    assert main.extract_tx_hashes(text) == [h]


def test_auto_tx_handler_triggers(main_module, monkeypatch):
    main = main_module
    h = "0x" + "b" * 64
    called = {}

    async def fake_tx_cmd(update, context):
        called["hash"] = context.args[0]

    monkeypatch.setattr(main, "tx_cmd", fake_tx_cmd)

    class DummyMsg:
        text = f"hash {h} here"
        caption = None
        photo = None
        document = None

    update = SimpleNamespace(effective_message=DummyMsg())
    context = SimpleNamespace()
    asyncio.run(main.auto_tx_handler(update, context))
    assert called.get("hash") == h