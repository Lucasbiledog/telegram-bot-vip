from __future__ import annotations
import os, time, hmac, hashlib, logging
from typing import Optional, Tuple, Dict
from contextlib import suppress
from datetime import datetime, timedelta, timezone

import json
from fastapi.responses import Response, JSONResponse


from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

from db import init_db, cfg_get, user_get_or_create, vip_upsert_and_get_until
from payments import resolve_payment_usd_autochain

# ----------------- LOG -----------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO))
LOG = logging.getLogger("main")

# -------------- ENV --------------------
load_dotenv()

BOT_TOKEN       = os.getenv("BOT_TOKEN")
SELF_URL        = os.getenv("SELF_URL", "").rstrip("/")
WEBHOOK_SECRET  = os.getenv("WEBHOOK_SECRET", "secret")
GROUP_VIP_ID    = int(os.getenv("GROUP_VIP_ID", "0"))
WEBAPP_URL      = os.getenv("WEBAPP_URL", "")                      # opcional (checkout web)
WEBAPP_LINK_SECRET = os.getenv("WEBAPP_LINK_SECRET", "change-me")  # p/ assinar deep-link

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN não definido")

# ------------- HELPERS -----------------
def make_link_sig(secret: str, uid: int, ts: int) -> str:
    msg = f"{uid}:{ts}".encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()

def normalize_tx_hash(s: str) -> Optional[str]:
    s = (s or "").strip()
    if not s:
        return None
    if s.startswith("0x") and len(s) == 66:
        return s.lower()
    return None

def get_vip_plan_prices_usd_sync(raw: Optional[str]) -> Dict[int, float]:
    """
    Lê do banco (JSON no formato dias:preço) ou usa os defaults.
    Defaults solicitados:
    30->0.05 ; 60->1.00 ; 180->1.50 ; 365->2.00
    """
    defaults = {30: 0.05, 60: 1.00, 180: 1.50, 365: 2.00}
    # se quiser permitir override por JSON, descomente e implemente parse
    return defaults

def choose_plan_from_usd(usd: float, price_table: Dict[int, float]) -> Optional[int]:
    """
    Seleciona o maior plano cujo preço <= usd pago.
    """
    ok = [(days, price) for days, price in price_table.items() if usd + 1e-9 >= price]
    if not ok:
        return None
    ok.sort(key=lambda x: x[1])  # por preço
    return max(ok, key=lambda x: x[1])[0]

async def create_one_time_invite(bot, chat_id: int, expire_seconds: int = 7200, member_limit: int = 1) -> str:
    expire_date = datetime.now(timezone.utc) + timedelta(seconds=expire_seconds)
    link = await bot.create_chat_invite_link(
        chat_id=chat_id,
        expire_date=int(expire_date.timestamp()),
        member_limit=member_limit,
        creates_join_request=False,
        name="VIP one-time"
    )
    return link.invite_link

# ----------------- FASTAPI / PTB -----------------
app = FastAPI()
# (opcional) servir um checkout estático, se você criar a pasta webapp
if os.path.isdir("./webapp"):
    app.mount("/pay", StaticFiles(directory="./webapp", html=True), name="pay")

application = ApplicationBuilder().token(BOT_TOKEN).build()

# -------- features de pagamento/VIP ----------
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
    chain = details.get("chain_name", "")
    msg = (
        f"Pagamento confirmado ({moeda} @ {chain}) — ${usd:.2f}.\n"
        f"Plano: {days} dias — VIP até {until.strftime('%d/%m/%Y %H:%M')} (UTC)\n\n"
        f"Convite VIP (1 uso, expira em 2h):\n{link}"
    )
    try:
        await application.bot.send_message(chat_id=tg_id, text=msg)
    except Exception:
        pass
    return True, msg

# ---------------- TELEGRAM HANDLERS ----------------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await user_get_or_create(u.id, u.username)
    await update.effective_message.reply_text(
        "Bem-vindo!\n"
        "1) Abra /checkout para ver a carteira e instruções.\n"
        "2) Transfira de qualquer rede suportada para a carteira informada.\n"
        "3) Envie /tx <hash_da_transacao> aqui.\n"
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
        texto = (
            "Checkout web não configurado.\n"
            "Após transferir para a carteira exibida pelo bot, envie /tx <hash>."
        )
        return await context.bot.send_message(chat_id=uid, text=texto)

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "💳 Checkout (carteira & instruções)",
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

# ------------------- FASTAPI ROUTES -------------------
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

# -------- lifecycle: initialize + set webhook ----------
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

@app.on_event("shutdown")
async def on_shutdown():
    with suppress(Exception):
        await application.stop()
    with suppress(Exception):
        await application.shutdown()



# ================== CONFIG JS PARA O FRONT ==================
@app.get("/pay/config.js")
async def pay_config_js():
    # preços default (pode ler do DB se quiser)
    prices = get_vip_plan_prices_usd_sync(await cfg_get("vip_plan_prices_usd"))
    cfg = {
        "wallet": os.getenv("WALLET_ADDRESS", ""),
        "apiVerify": "/api/tx/verify",
        "plans": [{"days": d, "usd": prices[d]} for d in sorted(prices.keys())],
        "web3auth": {
            "clientId": os.getenv("WEB3AUTH_CLIENT_ID", ""),
            "jwks": os.getenv("WEB3AUTH_JWKS", "https://api-auth.web3auth.io/.well-known/jwks.json"),
        },
    }
    js = f"window.BOTCONF = {json.dumps(cfg, separators=(',',':'))};"
    return Response(js, media_type="application/javascript")


# ================== API: VALIDAR HASH E LIBERAR VIP ==================
@app.post("/api/tx/verify")
async def api_tx_verify(req: Request):
    """
    Body JSON: { "hash": "...", "uid": 123, "ts": 169..., "sig": "..." }
    """
    try:
        body = await req.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid json")

    tx_hash = normalize_tx_hash(body.get("hash", ""))
    uid = body.get("uid")
    ts = body.get("ts")
    sig = body.get("sig", "")

    if not tx_hash:
        raise HTTPException(status_code=400, detail="invalid hash")
    if not isinstance(uid, int) or not isinstance(ts, int) or not sig:
        raise HTTPException(status_code=400, detail="missing uid/ts/sig")

    # valida a assinatura do deep-link (mesma usada no /checkout)
    expected = make_link_sig(WEBAPP_LINK_SECRET, uid, ts)
    if not hmac.compare_digest(expected, sig):
        raise HTTPException(status_code=403, detail="bad signature")

    # (opcional) expirar em 24h
    if abs(int(time.time()) - ts) > 24 * 3600:
        raise HTTPException(status_code=410, detail="link expired")

    # cria/atualiza usuário e processa pagamento
    # username vem vazio do web (ele receberá a msg pelo próprio bot depois)
    ok, msg = await approve_by_usd_and_invite(uid, None, tx_hash)

    return JSONResponse({"ok": bool(ok), "message": msg})
