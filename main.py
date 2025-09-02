# --- imports base ---
import os
import time
import asyncio
import logging
from contextlib import suppress
from typing import Optional, Tuple, Dict

from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from telegram.error import RetryAfter

import httpx
from jose import jwt

# --- nossos módulos ---
from db import init_db, cfg_get, user_get_or_create, vip_upsert_and_get_until
from utils import (
    get_vip_plan_prices_usd_sync,
    choose_plan_from_usd,
    create_one_time_invite,
    make_link_sig,
)
from payments import resolve_payment_usd_autochain

# -----------------------------------------------------------------------------
# Logging e ENV
# -----------------------------------------------------------------------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO))
LOG = logging.getLogger("main")

load_dotenv()  # carregue .env primeiro

BOT_TOKEN = os.getenv("BOT_TOKEN")
SELF_URL = os.getenv("SELF_URL", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "secret")
GROUP_VIP_ID = int(os.getenv("GROUP_VIP_ID", "0"))

WEBAPP_URL = os.getenv("WEBAPP_URL", "")
WEBAPP_LINK_SECRET = os.getenv("WEBAPP_LINK_SECRET", "change-me")

WEB3AUTH_CLIENT_ID = os.getenv("WEB3AUTH_CLIENT_ID", "")
WEB3AUTH_JWKS = os.getenv("WEB3AUTH_JWKS", "https://api-auth.web3auth.io/.well-known/jwks.json")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN não definido no ambiente.")

# -----------------------------------------------------------------------------
# FastAPI + Telegram Application
# -----------------------------------------------------------------------------
app = FastAPI()
app.mount("/pay", StaticFiles(directory="./webapp", html=True), name="pay")

application = ApplicationBuilder().token(BOT_TOKEN).build()

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def normalize_tx_hash(h: Optional[str]) -> Optional[str]:
    if not h:
        return None
    h = h.strip()
    if not h.startswith("0x"):
        return None
    if len(h) != 66:
        return None
    return h

async def prices_table() -> Dict[int, float]:
    raw = await cfg_get("vip_plan_prices_usd")
    return get_vip_plan_prices_usd_sync(raw)

async def approve_by_usd_and_invite(tg_id: int, username: Optional[str], tx_hash: str) -> Tuple[bool, str]:
    ok, info, usd, details = await resolve_payment_usd_autochain(tx_hash)
    if not ok:
        return False, info

    prices = await prices_table()
    days = choose_plan_from_usd(usd or 0.0, prices)
    if not days:
        tabela = ", ".join(f"{d}d=${p:.2f}" for d, p in sorted(prices.items()))
        return False, f"Valor em USD insuficiente (${usd:.2f}). Tabela: {tabela}"

    until = await vip_upsert_and_get_until(tg_id, username, days)
    link = await create_one_time_invite(application.bot, GROUP_VIP_ID, expire_seconds=7200, member_limit=1)
    moeda = details.get("token_symbol", "CRYPTO")
    msg = (
        f"Pagamento confirmado em {moeda} (${usd:.2f}).\n"
        f"Plano: {days} dias — VIP até {until.strftime('%d/%m/%Y %H:%M')}\n\n"
        f"Convite VIP (1 uso, expira em 2h):\n{link}"
    )
    with suppress(Exception):
        await application.bot.send_message(chat_id=tg_id, text=msg)
    return True, msg

# -----------------------------------------------------------------------------
# Telegram handlers
# -----------------------------------------------------------------------------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await user_get_or_create(u.id, u.username)
    await update.effective_message.reply_text(
        "Bem-vindo! Passos:\n"
        "1) Abra /checkout para ver a carteira e os planos.\n"
        "2) Transfira de qualquer rede suportada para a carteira informada.\n"
        "3) Envie /tx <hash_da_transacao>.\n"
        "O bot detecta a chain/moeda automaticamente e libera o VIP."
    )

async def comandos_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prices = await prices_table()
    tabela = "\n".join([f"- {d} dias: ${p:.2f}" for d, p in sorted(prices.items())])
    txt = ("Comandos:\n"
           "/checkout — ver carteira e planos\n"
           "/tx <hash> — validar o pagamento pelo hash\n\n"
           "Planos (USD):\n" + tabela)
    await update.effective_message.reply_text(txt)

async def checkout_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if update.effective_chat.type in ("group", "supergroup"):
        with suppress(Exception):
            await msg.delete()

    uid = update.effective_user.id
    ts = int(time.time())
    sig = make_link_sig(WEBAPP_LINK_SECRET, uid, ts)

    if not WEBAPP_URL:
        texto = "Abra /tx <hash> após transferir para a carteira exibida (WEBAPP_URL não configurada)."
        return await context.bot.send_message(chat_id=uid, text=texto)

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("💳 Checkout (instruções & carteira)",
                             web_app=WebAppInfo(url=f"{WEBAPP_URL}?uid={uid}&ts={ts}&sig={sig}"))
    ]])
    await context.bot.send_message(
        chat_id=uid,
        text="Abra o checkout para ver a carteira e os planos. Depois envie /tx <hash>.",
        reply_markup=kb,
    )

async def tx_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.effective_message.reply_text("Uso: /tx <hash>\nEx.: /tx 0xabc...def")
    tx_hash = normalize_tx_hash(context.args[0])
    if not tx_hash:
        return await update.effective_message.reply_text("Hash inválido.")
    uid = update.effective_user.id
    uname = update.effective_user.username
    ok, msg = await approve_by_usd_and_invite(uid, uname, tx_hash)
    await update.effective_message.reply_text(msg)

# registra handlers
application.add_handler(CommandHandler("start", start_cmd))
application.add_handler(CommandHandler("comandos", comandos_cmd))
application.add_handler(CommandHandler("checkout", checkout_cmd))
application.add_handler(CommandHandler("tx", tx_cmd))

# -----------------------------------------------------------------------------
# FastAPI routes (uma vez só)
# -----------------------------------------------------------------------------
@app.get("/keepalive")
async def keepalive():
    return PlainTextResponse("ok")

@app.post("/webhook/{secret}")
async def telegram_webhook(secret: str, request: Request):
    if secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="forbidden")
    data = await request.json()
    update = Update.de_json(data, application.bot)
    await application.process_update(update)
    return PlainTextResponse("ok")

# --- Web3Auth opcional (mantido p/ futuro) ---
_web3auth_jwks_cache = None

async def _fetch_web3auth_jwks():
    global _web3auth_jwks_cache
    if _web3auth_jwks_cache:
        return _web3auth_jwks_cache
    async with httpx.AsyncClient(timeout=10) as cli:
        r = await cli.get(WEB3AUTH_JWKS)
        r.raise_for_status()
        _web3auth_jwks_cache = r.json()
        return _web3auth_jwks_cache

async def verify_web3auth_idtoken(id_token: str) -> dict:
    jwks = await _fetch_web3auth_jwks()
    claims = jwt.decode(
        id_token, jwks, audience=WEB3AUTH_CLIENT_ID,
        options={"verify_aud": True, "verify_exp": True}
    )
    return claims

# -----------------------------------------------------------------------------
# Lifespan: startup/shutdown (initialize/start/stop/shutdown + webhook com retry)
# -----------------------------------------------------------------------------
@app.on_event("startup")
async def _startup():
    await init_db()

    # initialize + start PTB
    await application.initialize()
    with suppress(Exception):
        await application.start()

    # set webhook com até 3 tentativas, tratando Flood control
    if SELF_URL and WEBHOOK_SECRET:
        for i in range(3):
            try:
                await application.bot.set_webhook(url=f"{SELF_URL}/webhook/{WEBHOOK_SECRET}")
                LOG.info("Webhook setado em %s/webhook/%s", SELF_URL, WEBHOOK_SECRET)
                break
            except RetryAfter as e:
                delay = getattr(e, "retry_after", 1)
                LOG.warning("Flood control do Telegram. Tentando novamente em %ss...", delay)
                await asyncio.sleep(delay + 1)
            except Exception as e:
                LOG.error("Falha ao setar webhook: %s", e)
                break

@app.on_event("shutdown")
async def _shutdown():
    with suppress(Exception):
        await application.stop()
    with suppress(Exception):
        await application.shutdown()
