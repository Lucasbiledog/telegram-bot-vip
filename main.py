# main.py
from __future__ import annotations
import os, hmac, hashlib, time, logging, asyncio
from typing import Optional, Tuple, Dict
from datetime import datetime, timedelta, timezone
from contextlib import suppress
import asyncio
from contextlib import suppress


from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

from payments import resolve_payment_usd_autochain, get_wallet_address
from db import init_db, cfg_get, user_get_or_create, user_set_vip_until

# ------------------ LOG ------------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO),
                    format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
LOG = logging.getLogger("main")

# 1) .env antes de ler envs
load_dotenv()

# 2) envs
BOT_TOKEN = os.getenv("BOT_TOKEN")
SELF_URL = os.getenv("SELF_URL", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "secret")
GROUP_VIP_ID = int(os.getenv("GROUP_VIP_ID", "0"))
WEBAPP_URL = os.getenv("WEBAPP_URL", "")
WEBAPP_LINK_SECRET = os.getenv("WEBAPP_LINK_SECRET", "change-me")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN não definido no ambiente.")

# 3) FastAPI + static da webapp
app = FastAPI()
app.mount("/pay", StaticFiles(directory="./webapp", html=True), name="pay")

# 4) Telegram Application
application = ApplicationBuilder().token(BOT_TOKEN).build()

# -------------- Utils --------------

def make_link_sig(secret: str, uid: int, ts: int) -> str:
    msg = f"{uid}:{ts}".encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()

def normalize_tx_hash(h: str) -> Optional[str]:
    h = (h or "").strip()
    if not h:
        return None
    if not h.startswith("0x"):
        h = "0x" + h
    return h if len(h) == 66 else None

def get_vip_plan_prices_usd_sync(raw: Optional[str]) -> Dict[int, float]:
    """
    Formatos aceitos:
    - string "30:0.05,60:1,180:1.5,365:2"
    - None -> defaults
    """
    default = {30: 0.05, 60: 1.00, 180: 1.50, 365: 2.00}
    if not raw:
        return default
    table: Dict[int, float] = {}
    for part in raw.split(","):
        if ":" not in part:
            continue
        d, p = part.split(":", 1)
        try:
            d_i = int(d.strip())
            p_f = float(p.strip())
            if d_i > 0 and p_f >= 0:
                table[d_i] = p_f
        except Exception:
            continue
    return table or default

def choose_plan_from_usd(paid_usd: float, prices: Dict[int, float]) -> int:
    """
    Escolhe o maior plano cujo preço <= paid_usd (com pequena folga).
    """
    if paid_usd is None:
        return 0
    epsilon = 1e-9
    best_days = 0
    for days, price in sorted(prices.items(), key=lambda kv: (kv[1], kv[0])):
        if paid_usd + epsilon >= price:
            best_days = max(best_days, days)
    return best_days

async def prices_table() -> Dict[int, float]:
    raw = await cfg_get("vip_plan_prices_usd")
    return get_vip_plan_prices_usd_sync(raw)

async def create_one_time_invite(bot, chat_id: int, expire_seconds: int = 7200, member_limit: int = 1) -> str:
    from datetime import datetime, timedelta
    expire_date = datetime.now(timezone.utc) + timedelta(seconds=expire_seconds)
    link = await bot.create_chat_invite_link(chat_id=chat_id,
                                             expire_date=expire_date,
                                             member_limit=member_limit,
                                             creates_join_request=False)
    return link.invite_link

# memória simples para não duplicar respostas por mesmo tx_hash em curto período
_recent_tx_by_user: Dict[int, Dict[str, float]] = {}  # uid -> {tx_hash: ts}

def _is_duplicate(uid: int, tx_hash: str, ttl_seconds: int = 300) -> bool:
    now = time.time()
    info = _recent_tx_by_user.setdefault(uid, {})
    # purge antigos
    old = [k for k,v in info.items() if now - v > ttl_seconds]
    for k in old:
        info.pop(k, None)
    if tx_hash in info:
        return True
    info[tx_hash] = now
    return False

# -------------- Core: aprovar & convidar --------------

async def approve_by_usd_and_invite(tg_id: int, username: Optional[str], tx_hash: str) -> Tuple[bool, str]:
    ok, info, usd, details = await resolve_payment_usd_autochain(tx_hash)
    if not ok:
        return False, info

    # tabela de preços => days
    prices = await prices_table()
    days = choose_plan_from_usd(usd or 0.0, prices)
    if not days:
        tabela = ", ".join(f"{d}d=${p:.2f}" for d, p in sorted(prices.items()))
        return False, f"Valor em USD insuficiente (${(usd or 0.0):.2f}). Tabela: {tabela}"

    # calcula 'until'
    now = datetime.now(timezone.utc)
    until = now + timedelta(days=days)

    # grava VIP
    await user_get_or_create(tg_id, username)
    await user_set_vip_until(tg_id, until)

    # cria convite
    link = await create_one_time_invite(application.bot, GROUP_VIP_ID, expire_seconds=7200, member_limit=1)

    # moeda & rede para mensagem
    moeda = details.get("token_symbol") or "CRYPTO"
    chain_name = details.get("chain_id")
    # mapeie chain_id -> nome simples se quiser; aqui deixo como id/curto:
    msg = (
        f"Pagamento confirmado: {moeda} em {chain_name}\n"
        f"Total em USD: ${usd or 0.0:.2f}\n"
        f"Plano: {days} dias — VIP até {until.strftime('%d/%m/%Y %H:%M')}\n\n"
        f"Convite VIP (1 uso, expira em 2h):\n{link}"
    )
    return True, msg

# -------------- Telegram handlers --------------

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
    pr = await prices_table()
    tabela = "\n".join([f"- {d} dias: ${p:.2f}" for d,p in sorted(pr.items())])
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
        reply_markup=kb
    )

async def tx_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.effective_message.reply_text("Uso: /tx <hash>\nEx.: /tx 0xabc...def")
    tx_hash = normalize_tx_hash(context.args[0])
    if not tx_hash:
        return await update.effective_message.reply_text("Hash inválido.")

    uid = update.effective_user.id
    uname = update.effective_user.username or ""

    # evita duplicar resposta
    if _is_duplicate(uid, tx_hash):
        return await update.effective_message.reply_text("Esse hash já foi processado recentemente. Aguarde.")

    ok, msg = await approve_by_usd_and_invite(uid, uname, tx_hash)
    await update.effective_message.reply_text(msg)

# registra handlers
application.add_handler(CommandHandler("start", start_cmd))
application.add_handler(CommandHandler("comandos", comandos_cmd))
application.add_handler(CommandHandler("checkout", checkout_cmd))
application.add_handler(CommandHandler("tx", tx_cmd))

# -------------- API da webapp --------------

@app.get("/api/config")
async def api_config():
    try:
        wallet = get_wallet_address() or "-"
        pr = await prices_table()
        return JSONResponse({
            "ok": True,
            "wallet_address": wallet,
            "plans_usd": pr
        })
    except Exception as e:
        LOG.exception("api_config erro: %s", e)
        return JSONResponse({"ok": False, "error": "erro_ao_carregar"})

@app.post("/api/validate")
async def api_validate(request: Request):
    """
    Body: { "hash": "0x...", "uid": <int>, "uname": "<str>" }
    """
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="json inválido")

    tx_hash = normalize_tx_hash(str(data.get("hash", "")))
    if not tx_hash:
        raise HTTPException(status_code=400, detail="hash inválido")

    uid = int(data.get("uid") or 0)
    uname = str(data.get("uname") or "")

    # dedupe por api também
    if uid and _is_duplicate(uid, tx_hash):
        return JSONResponse({"ok": False, "msg": "Esse hash já foi processado recentemente. Aguarde."})

    ok, msg = await approve_by_usd_and_invite(uid, uname, tx_hash)
    return JSONResponse({"ok": ok, "msg": msg})

# -------------- Webhook & health --------------

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

# -------------- Startup / Shutdown --------------

async def _heartbeat():
    # simples logger para manter “vivo” no Render e ajudar debug
    while True:
        LOG.info("[heartbeat] app ativo; wallet=%s", get_wallet_address())
        await asyncio.sleep(60)

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

    # dispara heartbeat
    asyncio.create_task(_heartbeat())

@app.on_event("shutdown")
async def on_shutdown():
    with suppress(Exception):
        await application.stop()
    with suppress(Exception):
        await application.shutdown()
