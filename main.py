# main.py
from __future__ import annotations
import os
import time
import hmac
import json
import hashlib
import asyncio
import logging
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from telegram.ext import Application, ApplicationBuilder, CommandHandler, ContextTypes

# --- carrega .env antes de tudo
load_dotenv()

# --- logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO))
LOG = logging.getLogger("main")

# --- ENVs obrigatórias/úteis
BOT_TOKEN       = os.getenv("BOT_TOKEN")
SELF_URL        = os.getenv("SELF_URL", "").rstrip("/")
WEBHOOK_SECRET  = os.getenv("WEBHOOK_SECRET", "secret")
GROUP_VIP_ID    = int(os.getenv("GROUP_VIP_ID", "0"))  # id negativo p/ supergroup
WEBAPP_URL      = os.getenv("WEBAPP_URL", "")          # se usar link direto do Render
WEBAPP_LINK_SECRET = os.getenv("WEBAPP_LINK_SECRET", "change-me")
HEARTBEAT_SEC   = int(os.getenv("HEARTBEAT_SEC", "120"))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN não definido.")

# defaults da tabela de planos (USD)
DEFAULT_PRICES: Dict[int, float] = {30: 0.05, 60: 1.00, 180: 1.50, 365: 2.00}

# --- DB helpers
from db import init_db, cfg_get, user_get_or_create, user_set_vip_until

# --- payments (multi-chain)
from payments import resolve_payment_usd_autochain, WALLET_ADDRESS

# --- FastAPI
app = FastAPI()
# checkout estático (coloque seu index.html/JS/CSS em ./webapp)
app.mount("/pay", StaticFiles(directory="./webapp", html=True), name="pay")

@app.get("/")
async def root():
    # redireciona para o checkout
    return RedirectResponse(url="/pay")

@app.get("/keepalive")
async def keepalive():
    return PlainTextResponse("ok")

# -------- util: assinatura simples para link do webapp (opcional) --------
def make_link_sig(secret: str, uid: int, ts: int) -> str:
    mac = hmac.new(secret.encode(), f"{uid}:{ts}".encode(), hashlib.sha256).hexdigest()
    return mac

# -------- preços (podem vir do banco via cfg) --------
def get_vip_plan_prices_usd_sync(raw: Optional[str]) -> Dict[int, float]:
    """
    raw pode ser um JSON no banco, ex:
    {"30":0.05,"60":1.0,"180":1.5,"365":2.0}
    """
    if not raw:
        return dict(DEFAULT_PRICES)
    try:
        data = json.loads(raw)
        out: Dict[int, float] = {}
        for k, v in data.items():
            out[int(k)] = float(v)
        return out or dict(DEFAULT_PRICES)
    except Exception:
        return dict(DEFAULT_PRICES)

async def prices_table() -> Dict[int, float]:
    raw = await cfg_get("vip_plan_prices_usd")
    return get_vip_plan_prices_usd_sync(raw)

def choose_plan_from_usd(usd: float, table: Dict[int, float]) -> Optional[int]:
    """
    Escolhe o MAIOR plano cujo preço <= usd (ex.: pagou $1.2 → pega 60d a $1.0)
    """
    best_days = None
    best_price = -1.0
    for days, price in table.items():
        if usd >= price and price > best_price:
            best_days = days
            best_price = price
    return best_days

async def vip_upsert_and_get_until(tg_id: int, username: Optional[str], days: int) -> datetime:
    await user_get_or_create(tg_id, username or "")
    now = datetime.now(timezone.utc)
    until = now + timedelta(days=days)
    await user_set_vip_until(tg_id, until)
    return until

async def create_one_time_invite(bot, chat_id: int, expire_seconds: int = 7200, member_limit: int = 1) -> str:
    link = await bot.create_chat_invite_link(
        chat_id=chat_id,
        expire_date=int(time.time()) + expire_seconds,
        member_limit=member_limit
    )
    return link.invite_link

def normalize_tx_hash(s: str) -> Optional[str]:
    s = (s or "").strip()
    if not s:
        return None
    if s.startswith("0x") and len(s) == 66:
        return s
    # alguns explorers mostram "Hash: 0xabc..." — tenta extrair
    if "0x" in s:
        pos = s.find("0x")
        cand = s[pos:pos+66]
        if len(cand) == 66:
            return cand
    return None

# -------- API pública para o checkout --------
@app.get("/api/config")
async def api_config():
    prices = await prices_table()
    return {
        "wallet_address": WALLET_ADDRESS,
        "prices_usd": prices,
    }

@app.post("/api/validate_tx")
async def api_validate_tx(payload: dict):
    """
    payload: {"tx_hash": "0x...", "telegram_id": 12345, "username": "foo"}
    - Valida a transação em qualquer chain suportada (payments.py)
    - Converte para USD
    - Se atingir um plano, grava VIP e (se tiver telegram_id) envia convite
    """
    tx_hash = normalize_tx_hash((payload or {}).get("tx_hash", ""))
    if not tx_hash:
        return JSONResponse(status_code=400, content={"ok": False, "message": "Hash inválido."})

    ok, info, usd, details = await resolve_payment_usd_autochain(tx_hash)
    if not ok:
        return JSONResponse(status_code=400, content={"ok": False, "message": info, "details": details})

    prices = await prices_table()
    days = choose_plan_from_usd(usd or 0.0, prices)
    if not days:
        tabela = ", ".join(f"{d}d=${p:.2f}" for d, p in sorted(prices.items()))
        return JSONResponse(
            status_code=400,
            content={"ok": False, "message": f"Valor em USD insuficiente (${usd:.2f}). Tabela: {tabela}", "usd": usd, "details": details}
        )

    # Se veio telegram_id, já libera e envia convite
    tg_id = payload.get("telegram_id")
    username = payload.get("username")
    invite_link = None
    vip_until_iso = None
    if tg_id and GROUP_VIP_ID:
        until = await vip_upsert_and_get_until(int(tg_id), username, days)
        vip_until_iso = until.isoformat()
        with suppress(Exception):
            invite_link = await create_one_time_invite(application.bot, GROUP_VIP_ID, expire_seconds=7200, member_limit=1)
            await application.bot.send_message(
                chat_id=int(tg_id),
                text=(
                    f"✅ Pagamento confirmado (${usd:.2f}). Plano {days} dias.\n"
                    f"VIP ativo até {until.strftime('%d/%m/%Y %H:%M')}.\n"
                    f"Convite (1 uso, expira em 2h):\n{invite_link}"
                )
            )

    return {
        "ok": True,
        "message": f"Pagamento confirmado (${usd:.2f}). Plano {days} dias.",
        "usd": usd,
        "days": days,
        "invite_link": invite_link,
        "vip_until": vip_until_iso,
        "details": details,
    }

# -------- Telegram bot --------
application: Application = ApplicationBuilder().token(BOT_TOKEN).build()

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await user_get_or_create(u.id, u.username)
    await update.effective_message.reply_text(
        "Bem-vindo!\n\n"
        "1) Abra /checkout para ver a carteira e os planos.\n"
        "2) Transfira de QUALQUER rede suportada para a carteira mostrada.\n"
        "3) Envie /tx <hash> aqui ou valide no botão da página.\n"
        "O bot detecta rede & moeda automaticamente e libera o VIP. 🚀"
    )

async def comandos_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prices = await prices_table()
    tabela = "\n".join([f"- {d} dias: ${p:.2f}" for d, p in sorted(prices.items())])
    await update.effective_message.reply_text(
        "Comandos:\n"
        "/checkout — abrir a página de pagamento\n"
        "/tx <hash> — validar seu pagamento pelo hash\n\n"
        "Planos (USD):\n" + tabela
    )

async def checkout_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    # apaga no grupo
    if update.effective_chat and update.effective_chat.type in ("group", "supergroup"):
        with suppress(Exception):
            await msg.delete()

    uid = update.effective_user.id
    ts = int(time.time())
    sig = make_link_sig(WEBAPP_LINK_SECRET, uid, ts)

    if not WEBAPP_URL:
        # se estiver rodando no Render, o /pay já serve o index
        url = f"{SELF_URL}/pay?uid={uid}&ts={ts}&sig={sig}"
    else:
        url = f"{WEBAPP_URL}?uid={uid}&ts={ts}&sig={sig}"

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Abrir Checkout", web_app=WebAppInfo(url=url))]
    ])
    await context.bot.send_message(
        chat_id=uid,
        text="Use o botão abaixo para abrir o checkout:",
        reply_markup=kb
    )

async def tx_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.effective_message.reply_text("Uso: /tx <hash>\nEx.: /tx 0xabc...def")

    tx_hash = normalize_tx_hash(context.args[0])
    if not tx_hash:
        return await update.effective_message.reply_text("Hash inválido.")

    uid = update.effective_user.id
    uname = update.effective_user.username

    ok, info, usd, details = await resolve_payment_usd_autochain(tx_hash)
    if not ok:
        return await update.effective_message.reply_text(info)

    prices = await prices_table()
    days = choose_plan_from_usd(usd or 0.0, prices)
    if not days:
        tabela = ", ".join(f"{d}d=${p:.2f}" for d, p in sorted(prices.items()))
        return await update.effective_message.reply_text(
            f"Valor em USD insuficiente (${usd:.2f}). Tabela: {tabela}"
        )

    until = await vip_upsert_and_get_until(uid, uname, days)
    link = await create_one_time_invite(context.bot, GROUP_VIP_ID, expire_seconds=7200, member_limit=1)
    moeda = (details.get("token_symbol") or "CRYPTO")
    await update.effective_message.reply_text(
        f"Pagamento confirmado em {moeda} (${usd:.2f}).\n"
        f"Plano: {days} dias — VIP até {until.strftime('%d/%m/%Y %H:%M')}.\n\n"
        f"Convite VIP (1 uso, expira em 2h):\n{link}"
    )

application.add_handler(CommandHandler("start", start_cmd))
application.add_handler(CommandHandler("comandos", comandos_cmd))
application.add_handler(CommandHandler("checkout", checkout_cmd))
application.add_handler(CommandHandler("tx", tx_cmd))

# -------- Telegram webhook --------
@app.post("/webhook/{secret}")
async def telegram_webhook(secret: str, request: Request):
    if secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="forbidden")
    data = await request.json()
    update = Update.de_json(data, application.bot)
    await application.process_update(update)
    return PlainTextResponse("ok")

# -------- ciclo de vida (startup/shutdown) --------
async def _heartbeat():
    # pequeno heartbeat para deixar rastro nos logs
    while True:
        LOG.info("heartbeat: service alive, wallet=%s", WALLET_ADDRESS)
        await asyncio.sleep(HEARTBEAT_SEC)

@app.on_event("startup")
async def on_startup():
    await init_db()
    await application.initialize()
    with suppress(Exception):
        await application.start()

    if SELF_URL and WEBHOOK_SECRET:
        try:
            await application.bot.set_webhook(url=f"{SELF_URL}/webhook/{WEBHOOK_SECRET}")
            LOG.info("Webhook setado em %s/webhook/%s", SELF_URL, WEBHOOK_SECRET)
        except Exception as e:
            LOG.error("Falha ao setar webhook: %s", e)

    # inicia heartbeat
    asyncio.create_task(_heartbeat())

@app.on_event("shutdown")
async def on_shutdown():
    with suppress(Exception):
        await application.stop()
    with suppress(Exception):
        await application.shutdown()
