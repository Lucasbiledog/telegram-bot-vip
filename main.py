import os, logging, time
from typing import Optional, Tuple, Dict

from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

from jose import jwt
import httpx

from db import init_db, cfg_get, user_get_or_create
from utils import (
    get_vip_plan_prices_usd_sync,
    choose_plan_from_usd,
    vip_upsert_and_get_until,
    create_one_time_invite,
    make_link_sig
)
from payments import resolve_payment_usd

# === Load env vars ===
load_dotenv()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO))
LOG = logging.getLogger("main")

BOT_TOKEN = os.getenv("BOT_TOKEN")
BOT_USERNAME = os.getenv("BOT_USERNAME", "bot")
SELF_URL = os.getenv("SELF_URL", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "secret")

GROUP_VIP_ID = int(os.getenv("GROUP_VIP_ID","0"))
GROUP_FREE_ID = int(os.getenv("GROUP_FREE_ID","0"))

# WebApp / Web3Auth
WEBAPP_URL = os.getenv("WEBAPP_URL", "")
WEBAPP_LINK_SECRET = os.getenv("WEBAPP_LINK_SECRET", "change-me")
WEB3AUTH_CLIENT_ID = os.getenv("WEB3AUTH_CLIENT_ID", "")
WEB3AUTH_JWKS = os.getenv("WEB3AUTH_JWKS", "https://api.openlogin.com/jwks")

# Telegram app
application = ApplicationBuilder().token(BOT_TOKEN).build()

# FastAPI
app = FastAPI()
# Sirva seu WebApp estático (coloque index.html em ./webapp)
app.mount("/pay", StaticFiles(directory="./webapp", html=True), name="pay")

# ----------- Helpers ----------- #
def normalize_tx_hash(h: Optional[str]) -> Optional[str]:
    if not h: return None
    h = h.strip()
    if not h.startswith("0x"): return None
    if len(h) != 66: return None
    return h

async def prices_table() -> Dict[int, float]:
    raw = await cfg_get("vip_plan_prices_usd")
    return get_vip_plan_prices_usd_sync(raw)

# ... imports e config iguais ...

async def approve_by_usd_and_invite(tg_id: int, username: Optional[str], tx_hash: str, chain_id: Optional[str]) -> Tuple[bool, str]:
    ok, info, usd, details = await resolve_payment_usd(tx_hash, chain_id)
    if not ok:
        return False, info

    prices = await prices_table()
    days = choose_plan_from_usd(usd or 0.0, prices)
    if not days:
        tabela = ", ".join(f"{d}d=${p:.2f}" for d, p in sorted(prices.items()))
        return False, f"Valor em USD insuficiente (${usd:.2f}). Tabela: {tabela}"

    until = await vip_upsert_and_get_until(tg_id, username, days)
    link = await create_one_time_invite(application.bot, GROUP_VIP_ID, expire_seconds=7200, member_limit=1)

    # Mensagem amigável com a moeda detectada
    moeda = details.get("token_symbol", "CRYPTO")
    msg = (
        f"Pagamento confirmado em {moeda} (${usd:.2f}).\n"
        f"Plano: {days} dias — VIP até {until.strftime('%d/%m/%Y %H:%M')}\n\n"
        f"Convite VIP (1 uso, expira em 2h):\n{link}"
    )
    try:
        await application.bot.send_message(chat_id=tg_id, text=msg)
    except Exception:
        pass
    return True, msg

# /tx (manual) — se o usuário não informou chain, assume a principal (ex.: Polygon 0x89)
DEFAULT_CHAIN_ID = os.getenv("DEFAULT_CHAIN_ID", "0x89")

async def tx_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.effective_message.reply_text("Uso: /tx <hash> [chainId_hex]\nEx.: /tx 0xabc... 0x89")
    tx_hash = normalize_tx_hash(context.args[0])
    if not tx_hash:
        return await update.effective_message.reply_text("Hash inválido.")
    chain_id = context.args[1] if len(context.args) > 1 else DEFAULT_CHAIN_ID

    uid = update.effective_user.id
    uname = update.effective_user.username
    ok, msg = await approve_by_usd_and_invite(uid, uname, tx_hash, chain_id)
    await update.effective_message.reply_text(msg)

# /crypto_webhook — recebe chainId do WebApp
@app.post("/crypto_webhook")
async def crypto_webhook(request: Request):
    body = await request.json()
    tg_id = int(body.get("telegram_user_id"))
    tx_hash = body.get("tx_hash")
    chain_id = body.get("chain_id")   # <<<<<< ADICIONADO
    idt = body.get("web3auth_id_token")

    if idt and WEB3AUTH_CLIENT_ID:
        try:
            _ = await verify_web3auth_idtoken(idt)
        except Exception:
            raise HTTPException(status_code=401, detail="idToken inválido")

    if not tg_id or not tx_hash:
        raise HTTPException(status_code=400, detail="telegram_user_id/tx_hash inválidos")

    tx_hash = normalize_tx_hash(tx_hash)
    if not tx_hash:
        raise HTTPException(status_code=400, detail="hash inválido")

    ok, msg = await approve_by_usd_and_invite(tg_id, None, tx_hash, chain_id or os.getenv("DEFAULT_CHAIN_ID", "0x89"))
    return {"ok": ok, "message": msg}


# ----------- Handlers ----------- #
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await user_get_or_create(u.id, u.username)
    await update.effective_message.reply_text("Olá! Sou o bot VIP/Free. Use /pagar para assinar via Web3Auth, ou /tx <hash>.")

async def comandos_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prices = await prices_table()
    tabela = "\n".join([f"- {d} dias: ${p:.2f}" for d,p in sorted(prices.items())])
    txt = ("Comandos:\n"
           "/pagar — abrir WebApp com Web3Auth\n"
           "/tx <hash> — validar pagamento manualmente\n"
           "/getid — seu ID\n\n"
           "Planos (USD):\n" + tabela)
    await update.effective_message.reply_text(txt)

async def getid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    cid = update.effective_chat.id
    await update.effective_message.reply_text(f"Seu ID: {uid}\nChat ID: {cid}")

async def pagar_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if update.effective_chat.type in ("group", "supergroup"):
        try: await msg.delete()
        except: pass

    uid = update.effective_user.id
    ts = int(time.time())
    sig = make_link_sig(WEBAPP_LINK_SECRET, uid, ts)

    if not WEBAPP_URL:
        texto = ("Para assinar: envie cripto para a carteira configurada e depois use /tx <hash>.\n"
                 "WEBAPP_URL não configurada.")
        return await context.bot.send_message(chat_id=uid, text=texto)

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("💳 Pagar com Web3Auth (WebApp)",
                             web_app=WebAppInfo(url=f"{WEBAPP_URL}?uid={uid}&ts={ts}&sig={sig}"))
    ]])
    texto = ("Abra o WebApp para conectar via Web3Auth, enviar o pagamento (nativo ou token ERC-20) "
             "e confirmar automaticamente. Você receberá o convite VIP em seguida.")
    await context.bot.send_message(chat_id=uid, text=texto, reply_markup=kb)

async def tx_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        return await update.effective_message.reply_text("Uso: /tx <hash>")
    tx_hash = normalize_tx_hash(args[0])
    if not tx_hash:
        return await update.effective_message.reply_text("Hash inválido.")
    uid = update.effective_user.id
    uname = update.effective_user.username
    ok, msg = await approve_by_usd_and_invite(uid, uname, tx_hash)
    await update.effective_message.reply_text(msg)

# Registrar handlers
application.add_handler(CommandHandler("start", start_cmd))
application.add_handler(CommandHandler("comandos", comandos_cmd))
application.add_handler(CommandHandler("getid", getid_cmd))
application.add_handler(CommandHandler("pagar", pagar_cmd))
application.add_handler(CommandHandler("tx", tx_cmd))

# ----------- FastAPI routes ----------- #
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

# Web3Auth verification (opcional; WebApp pode enviar idToken)
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
    if not id_token:
        raise HTTPException(status_code=401, detail="idToken ausente")
    jwks = await _fetch_web3auth_jwks()
    claims = jwt.decode(id_token, jwks, audience=WEB3AUTH_CLIENT_ID,
                        options={"verify_aud": True, "verify_exp": True})
    return claims

@app.post("/crypto_webhook")
async def crypto_webhook(request: Request):
    body = await request.json()
    tg_id = int(body.get("telegram_user_id"))
    tx_hash = body.get("tx_hash")
    idt = body.get("web3auth_id_token")

    # opcional: verificação do idToken vindo do WebApp
    if idt and WEB3AUTH_CLIENT_ID:
        try:
            _ = await verify_web3auth_idtoken(idt)
        except Exception:
            raise HTTPException(status_code=401, detail="idToken inválido")

    if not tg_id or not tx_hash:
        raise HTTPException(status_code=400, detail="telegram_user_id/tx_hash inválidos")

    tx_hash = normalize_tx_hash(tx_hash)
    if not tx_hash:
        raise HTTPException(status_code=400, detail="hash inválido")

    ok, msg = await approve_by_usd_and_invite(tg_id, None, tx_hash)
    return {"ok": ok, "message": msg}

# startup: init DB + set webhook
@app.on_event("startup")
async def on_startup():
    await init_db()
    if SELF_URL and WEBHOOK_SECRET:
        try:
            await application.bot.set_webhook(url=f"{SELF_URL}/webhook/{WEBHOOK_SECRET}")
            LOG.info("Webhook setado em %s/webhook/%s", SELF_URL, WEBHOOK_SECRET)
        except Exception as e:
            LOG.error("Falha ao setar webhook: %s", e)
