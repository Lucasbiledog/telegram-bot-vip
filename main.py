# main.py
from __future__ import annotations
import os, time, hmac, hashlib, json, logging
from typing import Optional, Dict, Tuple
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException, Body
from fastapi.responses import PlainTextResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

from contextlib import suppress

# --- Carrega .env antes de tudo
load_dotenv()

# --- Logs
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO))
LOG = logging.getLogger("main")

# --- Env
BOT_TOKEN       = os.getenv("BOT_TOKEN") or ""
SELF_URL        = os.getenv("SELF_URL", "")
WEBHOOK_SECRET  = os.getenv("WEBHOOK_SECRET", "secret")
GROUP_VIP_ID    = int(os.getenv("GROUP_VIP_ID", "0"))
WEBAPP_URL      = os.getenv("WEBAPP_URL", "")
WEBAPP_LINK_SECRET = os.getenv("WEBAPP_LINK_SECRET", "change-me")
MIN_CONFIRMATIONS   = int(os.getenv("MIN_CONFIRMATIONS", "1"))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN não definido no ambiente.")

# --- App FastAPI + estáticos do checkout
app = FastAPI()
app.mount("/pay", StaticFiles(directory="./webapp", html=True), name="pay")

# --- Telegram Application
application = ApplicationBuilder().token(BOT_TOKEN).build()

# --- DB helpers
from db import init_db, cfg_get, user_get_or_create, user_set_vip_until

# --- Payments
from payments import resolve_payment_usd_autochain

# =============== Utils ===============

def _fmt_usd(x: float) -> str:
    return f"${x:.4f}" if x < 0.01 else f"${x:.2f}"

def make_link_sig(secret: str, uid: int, ts: int) -> str:
    msg = f"{uid}|{ts}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()

def normalize_tx_hash(s: str) -> Optional[str]:
    s = (s or "").strip()
    if s.startswith("0x") and len(s) == 66:
        return s.lower()
    return None

def _default_prices() -> Dict[int, float]:
    # fallback (os mesmos da sua UI)
    return {30: 0.05, 60: 1.00, 180: 1.50, 365: 2.00}

def get_vip_plan_prices_usd_sync(raw: Optional[str]) -> Dict[int, float]:
    """
    Aceita formatos:
      - JSON: {"30":0.05,"60":1.0,"180":1.5,"365":2.0}
      - CSV simples: 30:0.05,60:1.0,180:1.5,365:2.0
    """
    if not raw:
        return _default_prices()
    raw = raw.strip()
    try:
        obj = json.loads(raw)
        out: Dict[int, float] = {}
        for k, v in obj.items():
            out[int(k)] = float(v)
        return out or _default_prices()
    except Exception:
        pass
    out: Dict[int, float] = {}
    try:
        parts = [p for p in raw.split(",") if p.strip()]
        for p in parts:
            k, v = p.split(":")
            out[int(k.strip())] = float(v.strip())
        return out or _default_prices()
    except Exception:
        return _default_prices()

def choose_plan_from_usd(usd: float, prices: Dict[int, float]) -> Optional[int]:
    # escolhe o MAIOR plano cujo preço <= usd
    best_days = None
    for days, price in sorted(prices.items(), key=lambda x: x[1]):
        if usd + 1e-12 >= float(price):
            best_days = days
    return best_days

async def prices_table() -> Dict[int, float]:
    raw = await cfg_get("vip_plan_prices_usd")
    return get_vip_plan_prices_usd_sync(raw)

async def vip_upsert_and_get_until(tg_id: int, username: Optional[str], days: int) -> datetime:
    # upsert + retorna a data final
    until = datetime.now(timezone.utc) + timedelta(days=days)
    await user_get_or_create(tg_id, username or "")
    await user_set_vip_until(tg_id, until)
    return until

async def create_one_time_invite(bot, chat_id: int, expire_seconds: int = 7200, member_limit: int = 1) -> str:
    """
    Requer que o BOT seja admin no grupo/supergrupo.
    """
    try:
        link = await bot.create_chat_invite_link(
            chat_id=chat_id,
            expire_date=int(time.time()) + int(expire_seconds),
            member_limit=member_limit,
            creates_join_request=False
        )
        return link.invite_link
    except Exception as e:
        LOG.error("Falha ao criar invite: %s", e)
        # fallback: link de chat (se existir)
        return "Invite indisponível — verifique se o bot é admin do grupo."

# =============== Fluxo de aprovação ===============

async def approve_by_usd_and_invite(tg_id: int, username: Optional[str], tx_hash: str) -> Tuple[bool, str]:
    ok, info, usd, details = await resolve_payment_usd_autochain(tx_hash)

    LOG.info("[approve] ok=%s usd=%s details=%s info=%s", ok, usd, details, info)

    if not ok:
        if os.getenv("DEBUG_PAYMENTS", "0") == "1" and details:
            pretty = json.dumps(details, ensure_ascii=False, indent=2)
            return False, f"{info}\n\nDEBUG:\n{pretty}"
        return False, info

    prices = await prices_table()
    days = choose_plan_from_usd(usd or 0.0, prices)
    if not days:
        tabela = "\n".join(f"- {d} dias: {_fmt_usd(p)}" for d, p in sorted(prices.items()))
        paid_txt = _fmt_usd(usd or 0.0)
        simb = details.get("token_symbol", "TOKEN")
        chain = details.get("chain_name", details.get("chain_id", ""))
        return False, (
            "Valor em USD insuficiente para algum plano.\n\n"
            f"Moeda: {simb} — Rede: {chain}\n"
            f"Total pago: {paid_txt}\n\n"
            "Planos disponíveis:\n" + tabela
        )

    until = await vip_upsert_and_get_until(tg_id, username, days)
    link = await create_one_time_invite(application.bot, GROUP_VIP_ID, expire_seconds=7200, member_limit=1)

    simb = details.get("token_symbol", "CRYPTO")
    chain = details.get("chain_name", details.get("chain_id", ""))
    paid_txt = _fmt_usd(usd or 0.0)

    msg = (
        f"Pagamento confirmado: {simb} em {chain}\n"
        f"Total em USD: {paid_txt}\n"
        f"Plano: {days} dias — VIP até {until.strftime('%d/%m/%Y %H:%M')}\n\n"
        f"Convite VIP (1 uso, expira em 2h):\n{link}"
    )
    try:
        await application.bot.send_message(chat_id=tg_id, text=msg)
    except Exception:
        pass
    return True, msg

# =============== Telegram handlers ===============

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await user_get_or_create(u.id, u.username)
    await update.effective_message.reply_text(
        "Bem-vindo! Passos:\n"
        "1) Abra /checkout para ver a carteira e os planos.\n"
        "2) Transfira de qualquer rede suportada para a carteira informada.\n"
        "3) Envie /tx <hash_da_transacao>.\n"
        "O bot detecta chain & moeda automaticamente e libera o VIP."
    )

async def comandos_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prices = await prices_table()
    tabela = "\n".join([f"- {d} dias: {_fmt_usd(p)}" for d,p in sorted(prices.items())])
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
        texto = "WEBAPP_URL não configurada. Depois de enviar, use /tx <hash> para validar."
        return await context.bot.send_message(chat_id=uid, text=texto)

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "💳 Checkout (instruções & carteira)",
            web_app=WebAppInfo(url=f"{WEBAPP_URL}?uid={uid}&ts={ts}&sig={sig}")
        )
    ]])
    await context.bot.send_message(
        chat_id=uid,
        text="Abra o checkout para ver a carteira e os planos. Depois envie /tx <hash>.",
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
    ok, msg = await approve_by_usd_and_invite(uid, uname, tx_hash)
    await update.effective_message.reply_text(msg)

application.add_handler(CommandHandler("start", start_cmd))
application.add_handler(CommandHandler("comandos", comandos_cmd))
application.add_handler(CommandHandler("checkout", checkout_cmd))
application.add_handler(CommandHandler("tx", tx_cmd))

# =============== API para a WebApp ===============

@app.get("/api/config")
async def api_config():
    """
    Fornece dados para a página de checkout:
    - carteira destino
    - tabela de planos
    - min confirmations
    """
    prices = await prices_table()
    plans = [{"days": d, "usd": float(p)} for d, p in sorted(prices.items())]
    from payments import WALLET_ADDRESS  # evitar import circular no topo
    return {
        "wallet": WALLET_ADDRESS,
        "plans": plans,
        "min_confirmations": MIN_CONFIRMATIONS,
    }

@app.post("/api/validate")
async def api_validate(payload: dict = Body(...)):
    tx_hash = str(payload.get("tx_hash", "")).strip()
    if not tx_hash:
        return {"ok": False, "message": "tx_hash obrigatório"}

    ok, msg, usd, details = await resolve_payment_usd_autochain(tx_hash)
    resp = {
        "ok": ok,
        "message": msg,
        "usd_value": float(usd or 0.0),
        "usd_pretty": _fmt_usd(usd or 0.0),
        "details": details if os.getenv("DEBUG_PAYMENTS", "0") == "1" else {},
    }
    return JSONResponse(resp)

# compat antiga (/api/validate_tx)
@app.post("/api/validate_tx")
async def api_validate_tx(payload: dict = Body(...)):
    return await api_validate(payload)

# =============== Infra: webhook + lifecycle ===============

@app.on_event("startup")
async def on_startup():
    LOG.info("Starting up...")
    await init_db()
    with suppress(Exception):
        await application.initialize()
        await application.start()
    if SELF_URL and WEBHOOK_SECRET:
        try:
            await application.bot.set_webhook(url=f"{SELF_URL}/webhook/{WEBHOOK_SECRET}")
            LOG.info("Webhook setado em %s/webhook/%s", SELF_URL, WEBHOOK_SECRET)
        except Exception as e:
            LOG.error("Falha ao setar webhook: %s", e)

@app.on_event("shutdown")
async def on_shutdown():
    with suppress(Exception):
        await application.stop()
    with suppress(Exception):
        await application.shutdown()

@app.get("/keepalive")
async def keepalive():
    # útil pro Render não hibernar, e p/ você enxergar rapidamente que está de pé
    LOG.info("[keepalive] tick")
    return PlainTextResponse("ok")

@app.post("/webhook/{secret}")
async def telegram_webhook(secret: str, request: Request):
    if secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="forbidden")
    data = await request.json()
    update = Update.de_json(data, application.bot)
    await application.process_update(update)
    return PlainTextResponse("ok")

# Raiz só para não dar 404 no scan do Render
@app.get("/")
async def root():
    return {"ok": True, "service": "telegram-vip-bot", "docs": "/pay"}
