import os, time, logging, asyncio
from contextlib import suppress
from typing import Optional, Dict, Tuple

from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# ====== SUAS FUNÇÕES DE DB ======
# espere que você já as tenha; apenas importamos
from db import init_db, cfg_get, user_get_or_create, vip_upsert_and_get_until

# ====== PAGAMENTOS (nosso módulo abaixo) ======
from payments import (
    resolve_payment_usd_autochain,
    get_wallet_address,
    get_prices_sync,
)

# -----------------------------------------------------------------------------------
# LOG e ENV
# -----------------------------------------------------------------------------------
load_dotenv()
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO))
LOG = logging.getLogger("main")

BOT_TOKEN = os.getenv("BOT_TOKEN")
SELF_URL = os.getenv("SELF_URL", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "secret")
GROUP_VIP_ID = int(os.getenv("GROUP_VIP_ID", "0"))
WEBAPP_URL = os.getenv("WEBAPP_URL", "")  # ex.: https://<seu-servico>.onrender.com/pay/

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN não definido.")

# -----------------------------------------------------------------------------------
# FASTAPI + STATIC
# -----------------------------------------------------------------------------------
app = FastAPI()
app.mount("/pay", StaticFiles(directory="./webapp", html=True), name="pay")

# -----------------------------------------------------------------------------------
# TELEGRAM (PTB)
# -----------------------------------------------------------------------------------
application = ApplicationBuilder().token(BOT_TOKEN).build()

# -----------------------------------------------------------------------------------
# HELPERS
# -----------------------------------------------------------------------------------
def normalize_tx_hash(s: str) -> Optional[str]:
    s = (s or "").strip()
    if s.startswith("0x") and len(s) == 66:
        return s.lower()
    return None

def make_link_sig(secret: str, uid: int, ts: int) -> str:
    import hashlib
    payload = f"{uid}:{ts}:{secret}".encode()
    return hashlib.sha256(payload).hexdigest()

# -----------------------------------------------------------------------------------
# PREÇOS (planos) – lido do Config do DB (string JSON ou "30:0.05,60:1,180:1.5,365:2")
# -----------------------------------------------------------------------------------
async def prices_table() -> Dict[int, float]:
    raw = await cfg_get("vip_plan_prices_usd")
    return get_prices_sync(raw)  # usa a função do payments que parseia

def choose_plan_from_usd(usd: float, table: Dict[int, float]) -> int:
    # escolhe o MAIOR plano cujo preço <= usd
    ans_days = 0
    for d, p in table.items():
        if usd + 1e-9 >= p and d > ans_days:
            ans_days = d
    return ans_days

# -----------------------------------------------------------------------------------
# Negócio: aprova hash e gera convite (NÃO envia msg; devolve o texto)
# -----------------------------------------------------------------------------------
async def approve_by_usd_and_invite(
    tg_id: int,
    username: Optional[str],
    tx_hash: str
) -> Tuple[bool, str]:
    ok, info, usd, details = await resolve_payment_usd_autochain(tx_hash)
    if not ok:
        return False, info

    prices = await prices_table()
    days = choose_plan_from_usd(usd or 0.0, prices)
    if not days:
        tabela = ", ".join(f"{d}d=${p:.2f}" for d, p in sorted(prices.items()))
        return False, f"Valor em USD insuficiente (${usd:.2f}). Tabela: {tabela}"

    until = await vip_upsert_and_get_until(tg_id, username, days)

    # convite com 1 uso, expira em 2h
    try:
        link = await application.bot.create_chat_invite_link(
            chat_id=GROUP_VIP_ID, creates_join_request=False,
            expire_date=int(time.time()) + 7200, member_limit=1
        )
        invite_url = link.invite_link
    except Exception:
        # fallback simples
        invite_url = "https://t.me/+seuGrupo"  # opcional

    moeda = details.get("token_symbol", "CRYPTO")
    chain = details.get("chain_name", "")
    msg = (
        f"Pagamento confirmado: {moeda} em {chain}\n"
        f"Total em USD: ${usd:.2f}\n"
        f"Plano: {days} dias — VIP até {until.strftime('%d/%m/%Y %H:%M')}\n\n"
        f"Convite VIP (1 uso, expira em 2h):\n{invite_url}"
    )
    return True, msg

# -----------------------------------------------------------------------------------
# Telegram Handlers
# -----------------------------------------------------------------------------------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await user_get_or_create(u.id, u.username)
    await update.effective_message.reply_text(
        "Bem-vindo! Passos:\n"
        "1) Abra /checkout para ver carteira & planos.\n"
        "2) Envie a cripto pra carteira.\n"
        "3) Cole /tx <hash_da_transacao>.\n"
        "O bot detecta chain & moeda automaticamente."
    )

async def checkout_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # botão WebApp (abre /pay)
    uid = update.effective_user.id
    ts = int(time.time())
    secret = os.getenv("WEBAPP_LINK_SECRET", "change-me")
    sig = make_link_sig(secret, uid, ts)
    url = WEBAPP_URL or f"{SELF_URL}/pay/"
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(
        "💳 Checkout (instruções & carteira)",
        web_app=WebAppInfo(url=f"{url}?uid={uid}&ts={ts}&sig={sig}")
    )]])
    await update.effective_message.reply_text(
        "Abra o checkout para ver a carteira e os planos. Depois envie /tx <hash>.",
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
    await update.effective_message.reply_text(msg)  # <- único lugar que envia

application.add_handler(CommandHandler("start", start_cmd))
application.add_handler(CommandHandler("checkout", checkout_cmd))
application.add_handler(CommandHandler("tx", tx_cmd))

# -----------------------------------------------------------------------------------
# API p/ a WebApp
# -----------------------------------------------------------------------------------
class ValidateReq(BaseModel):
    hash: str
    uid: Optional[int] = None

@app.get("/api/config")
async def api_config():
    return {
        "wallet": get_wallet_address(),
        "prices_usd": get_prices_sync(await cfg_get("vip_plan_prices_usd")),
        "min_confirmations": int(os.getenv("MIN_CONFIRMATIONS", "1")),
    }

@app.post("/api/validate")
async def api_validate(req: ValidateReq):
    tx_hash = normalize_tx_hash(req.hash)
    if not tx_hash:
        return JSONResponse({"ok": False, "error": "hash inválido"}, status_code=400)

    uid = req.uid or 0
    ok, msg = await approve_by_usd_and_invite(uid, None, tx_hash)
    return {"ok": ok, "message": msg}

# -----------------------------------------------------------------------------------
# Infra (startup/shutdown, webhook)
# -----------------------------------------------------------------------------------
@app.on_event("startup")
async def on_startup():
    LOG.info("Starting up...")
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

@app.on_event("shutdown")
async def on_shutdown():
    with suppress(Exception):
        await application.stop()
    with suppress(Exception):
        await application.shutdown()

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
