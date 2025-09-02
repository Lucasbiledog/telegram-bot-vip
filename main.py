# main.py
import os, time, asyncio, logging
from contextlib import suppress
from typing import Dict, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

# ---- LOGGING ---------------------------------------------------------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s"
)
LOG = logging.getLogger("main")

# ---- ENV -------------------------------------------------------------------
load_dotenv()

BOT_TOKEN       = os.getenv("BOT_TOKEN", "")
SELF_URL        = os.getenv("SELF_URL", "")
WEBHOOK_SECRET  = os.getenv("WEBHOOK_SECRET", "secret")
GROUP_VIP_ID    = int(os.getenv("GROUP_VIP_ID", "0"))
WEBAPP_URL      = os.getenv("WEBAPP_URL", "")  # exibe no /checkout
WEBAPP_LINK_SECRET = os.getenv("WEBAPP_LINK_SECRET", "change-me")

DEBUG_PAYMENTS  = os.getenv("DEBUG_PAYMENTS", "0") == "1"
MIN_CONFIRMATIONS = int(os.getenv("MIN_CONFIRMATIONS", "1"))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN não definido no ambiente")

# ---- IMPORTS LOCAIS (depois das ENVs) --------------------------------------
from db import init_db, cfg_get, user_get_or_create  # use só o que já existe
from payments import resolve_payment_usd_autochain, get_wallet_address

# Helpers opcionais que você já tinha em utils (ajuste se necessário)
def make_link_sig(secret: str, uid: int, ts: int) -> str:
    import hmac, hashlib
    return hmac.new(secret.encode(), f"{uid}:{ts}".encode(), hashlib.sha256).hexdigest()

def normalize_tx_hash(h: str) -> Optional[str]:
    h = (h or "").strip()
    if len(h) == 66 and h.startswith("0x"):
        return h.lower()
    return None

# ---- FASTAPI ---------------------------------------------------------------
app = FastAPI(title="VIP Bot API")

# serve sua SPA /pay a partir da pasta ./webapp
if os.path.isdir("./webapp"):
    app.mount("/pay", StaticFiles(directory="./webapp", html=True), name="pay")

# ---- TELEGRAM --------------------------------------------------------------
application = ApplicationBuilder().token(BOT_TOKEN).build()

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await user_get_or_create(u.id, u.username)
    await update.effective_message.reply_text(
        "Bem-vindo! Passos:\n"
        "1) Abra /checkout para ver a carteira e os planos.\n"
        "2) Transfira para a carteira informada.\n"
        "3) Cole o hash em /tx <hash> ou valide pelo botão no checkout."
    )

async def checkout_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if update.effective_chat.type in ("group", "supergroup"):
        with suppress(Exception):
            await msg.delete()

    uid = update.effective_user.id
    ts  = int(time.time())
    sig = make_link_sig(WEBAPP_LINK_SECRET, uid, ts)

    if not WEBAPP_URL:
        return await context.bot.send_message(
            chat_id=uid,
            text="WEBAPP_URL não configurada. Envie /tx <hash> após transferir."
        )

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "💳 Checkout (instruções & carteira)",
            web_app=WebAppInfo(url=f"{WEBAPP_URL}?uid={uid}&ts={ts}&sig={sig}")
        )
    ]])
    await context.bot.send_message(
        chat_id=uid,
        text="Abra o checkout para ver a carteira e os planos. Depois envie /tx <hash> ou valide no botão.",
        reply_markup=kb
    )

async def tx_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.effective_message.reply_text("Uso: /tx <hash>")
    tx_hash = normalize_tx_hash(context.args[0])
    if not tx_hash:
        return await update.effective_message.reply_text("Hash inválido.")

    ok, msg, usd, details = await resolve_payment_usd_autochain(tx_hash)
    if DEBUG_PAYMENTS:
        pretty = { "ok": ok, "msg": msg, "usd": usd, "details": details }
        LOG.info("DEBUG /tx: %s", pretty)

    await update.effective_message.reply_text(msg)

application.add_handler(CommandHandler("start", start_cmd))
application.add_handler(CommandHandler("checkout", checkout_cmd))
application.add_handler(CommandHandler("tx", tx_cmd))

# ---- LIFESPAN / STARTUP-SHUTDOWN ------------------------------------------
async def _heartbeat():
    """Evita inatividade do Render e ajuda na observabilidade."""
    while True:
        try:
            LOG.info("heartbeat: app vivo, webhook=%s", bool(SELF_URL))
        except Exception:
            pass
        await asyncio.sleep(60 if not DEBUG_PAYMENTS else 20)

@app.on_event("startup")
async def on_startup():
    LOG.info("Starting up...")
    await init_db()

    # Telegram
    await application.initialize()
    with suppress(Exception):
        await application.start()

    if SELF_URL and WEBHOOK_SECRET:
        try:
            await application.bot.set_webhook(url=f"{SELF_URL}/webhook/{WEBHOOK_SECRET}")
            LOG.info("Webhook setado em %s/webhook/%s", SELF_URL, WEBHOOK_SECRET)
        except Exception as e:
            LOG.error("Falha ao setar webhook: %s", e)

    # heartbeat
    asyncio.create_task(_heartbeat())

@app.on_event("shutdown")
async def on_shutdown():
    with suppress(Exception):
        await application.stop()
    with suppress(Exception):
        await application.shutdown()

# ---- ROTAS BÁSICAS ---------------------------------------------------------
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

# ---- API para o FRONT ------------------------------------------------------
@app.get("/api/config")
async def api_config():
    """
    Usado pelo checkout para:
    - Mostrar carteira
    - Exibir a tabela de preços (se configurada)
    - Mostrar MIN_CONFIRMATIONS
    """
    wallet = get_wallet_address() or ""
    prices_raw = await cfg_get("vip_plan_prices_usd")
    # formato recomendado no cfg: "30:0.05,60:1,180:1.5,365:2"
    prices: Dict[int, float] = {}
    if prices_raw:
        for it in prices_raw.split(","):
            if not it.strip():
                continue
            d, p = it.split(":")
            prices[int(d)] = float(p)
    else:
        prices = {30:0.05, 60:1.0, 180:1.5, 365:2.0}  # defaults

    return JSONResponse({
        "wallet": wallet or "erro ao carregar",
        "prices_usd": prices,
        "min_confirmations": MIN_CONFIRMATIONS,
        "debug": bool(DEBUG_PAYMENTS),
    })

@app.post("/api/validate_tx")
async def api_validate_alias(request: Request):
    """
    Alguns builds do front ainda chamam /api/validate_tx.
    Fazemos um redirect 307 para /api/validate para manter compatibilidade.
    """
    return RedirectResponse(url="/api/validate", status_code=307)

@app.post("/api/validate")
async def api_validate(request: Request):
    """
    Body: { "tx_hash": "0x..." }
    Retorna: { ok, message, usd, details } (+ debug quando habilitado)
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, detail="JSON inválido")

    tx_hash = normalize_tx_hash(body.get("tx_hash"))
    if not tx_hash:
        raise HTTPException(400, detail="tx_hash inválido")

    ok, msg, usd, details = await resolve_payment_usd_autochain(tx_hash)
    resp = {"ok": ok, "message": msg, "usd": usd, "details": details}
    if DEBUG_PAYMENTS:
        resp["env"] = {
            "wallet": get_wallet_address(),
            "min_confirmations": MIN_CONFIRMATIONS
        }
    return JSONResponse(resp)
