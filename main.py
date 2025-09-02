# --- no topo ---
import os, time, asyncio, logging
from typing import Optional, Tuple, Dict
from contextlib import suppress

from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

from db import init_db, cfg_get, user_get_or_create, user_set_vip_until
from models import User
from payments import resolve_payment_usd_autochain

load_dotenv()
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO))
LOG = logging.getLogger("main")

BOT_TOKEN = os.getenv("BOT_TOKEN")
SELF_URL = os.getenv("SELF_URL", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "secret")
GROUP_VIP_ID = int(os.getenv("GROUP_VIP_ID", "0"))
WEBAPP_URL = os.getenv("WEBAPP_URL", "")  # ex: https://telegram-bot-vip-hfn7.onrender.com/pay
WALLET_ADDRESS = (os.getenv("WALLET_ADDRESS") or "").strip()

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN não definido")

# --- FastAPI app ---
app = FastAPI()
# CORS p/ a página /pay falar com /api/validate
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # restrinja p/ seu domínio se quiser
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# arquivos estáticos do checkout
app.mount("/pay", StaticFiles(directory="./webapp", html=True), name="pay")

# Telegram Application
application = ApplicationBuilder().token(BOT_TOKEN).build()

# ========= Helpers de VIP =========
async def get_vip_plan_prices_usd() -> Dict[int, float]:
    # default: 30=0.05 | 60=1.00 | 180=1.50 | 365=2.00
    raw = await cfg_get("vip_plan_prices_usd")
    if not raw:
        return {30:0.05, 60:1.00, 180:1.50, 365:2.00}
    # formato "30:0.05,60:1,180:1.5,365:2"
    out: Dict[int, float] = {}
    for part in raw.split(","):
        d,p = part.split(":")
        out[int(d.strip())] = float(p.strip())
    return out

def choose_plan_from_usd(usd: float, table: Dict[int, float]) -> Optional[int]:
    # pega o MAIOR plano cujo preço <= usd
    ok = [d for d, price in table.items() if usd + 1e-9 >= price]
    return max(ok) if ok else None

async def vip_upsert_and_get_until(tg_id: int, username: Optional[str], days: int):
    from datetime import datetime, timedelta, timezone
    await user_get_or_create(tg_id, username)
    now = datetime.now(timezone.utc)
    until = now + timedelta(days=days)
    await user_set_vip_until(tg_id, until)
    return until

async def create_one_time_invite(bot, chat_id: int, expire_seconds=7200, member_limit=1):
    if not chat_id:
        return "Grupo VIP não configurado."
    try:
        inv = await bot.create_chat_invite_link(chat_id=chat_id, expire_date=None,
                                                creates_join_request=False,
                                                member_limit=member_limit)
        # Telegram ignora expire_seconds direto; se quiser expirar de verdade, trate com revoke depois
        return inv.invite_link
    except Exception as e:
        LOG.error("invite error: %s", e)
        return "Não foi possível gerar convite agora."

async def approve_by_usd_and_invite(tg_id: int, username: Optional[str], tx_hash: str) -> Tuple[bool, str]:
    ok, info, usd, details = await resolve_payment_usd_autochain(tx_hash)
    if not ok:
        return False, info

    prices = await get_vip_plan_prices_usd()
    days = choose_plan_from_usd(usd or 0.0, prices)
    if not days:
        tabela = ", ".join(f"{d}d=${p:.2f}" for d, p in sorted(prices.items()))
        return False, f"Valor insuficiente (${usd:.2f}). Tabela: {tabela}"

    until = await vip_upsert_and_get_until(tg_id, username, days)
    link = await create_one_time_invite(application.bot, GROUP_VIP_ID, expire_seconds=7200, member_limit=1)
    moeda = details.get("token_symbol", "CRYPTO")
    msg = (
        f"Pagamento confirmado em {moeda} (${usd:.2f}).\n"
        f"Plano: {days} dias — VIP até {until.strftime('%d/%m/%Y %H:%M')} UTC\n\n"
        f"Convite VIP (1 uso):\n{link}"
    )
    try:
        await application.bot.send_message(chat_id=tg_id, text=msg)
    except Exception:
        pass
    return True, msg

# ========= Handlers Telegram =========
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await user_get_or_create(u.id, u.username)
    await update.effective_message.reply_text(
        "Bem-vindo!\n"
        "1) Abra /checkout para ver a carteira e os planos.\n"
        "2) Transfira para a carteira.\n"
        "3) Envie /tx <hash> ou use o botão Validar na página."
    )

async def comandos_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prices = await get_vip_plan_prices_usd()
    tabela = "\n".join([f"- {d} dias: ${p:.2f}" for d,p in sorted(prices.items())])
    await update.effective_message.reply_text("Comandos:\n/checkout\n/tx <hash>\n\nPlanos:\n"+tabela)

async def checkout_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not WEBAPP_URL:
        return await update.effective_message.reply_text("WEBAPP_URL não configurada.")
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("💳 Abrir checkout", web_app=WebAppInfo(url=f"{WEBAPP_URL}?uid={uid}"))
    ]])
    await update.effective_message.reply_text("Abra o checkout:", reply_markup=kb)

async def tx_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.effective_message.reply_text("Uso: /tx <hash>\nEx.: /tx 0xabc...def")
    tx_hash = context.args[0].strip()
    uid = update.effective_user.id
    uname = update.effective_user.username
    ok, msg = await approve_by_usd_and_invite(uid, uname, tx_hash)
    await update.effective_message.reply_text(msg)

application.add_handler(CommandHandler("start", start_cmd))
application.add_handler(CommandHandler("comandos", comandos_cmd))
application.add_handler(CommandHandler("checkout", checkout_cmd))
application.add_handler(CommandHandler("tx", tx_cmd))

# ========= API p/ a página validar =========
@app.post("/api/validate")
async def api_validate(req: Request):
    data = await req.json()
    uid = int(data.get("uid", 0))
    tx_hash = (data.get("hash") or "").strip()
    if not uid or not tx_hash:
        return JSONResponse({"ok": False, "msg": "Parâmetros inválidos."}, status_code=400)

    # opcional: user_exists = await user_get_or_create(uid)
    ok, msg = await approve_by_usd_and_invite(uid, None, tx_hash)
    return JSONResponse({"ok": ok, "msg": msg})

# ========= Webhook Telegram =========
@app.post("/webhook/{secret}")
async def telegram_webhook(secret: str, request: Request):
    if secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="forbidden")
    data = await request.json()
    update = Update.de_json(data, application.bot)
    await application.process_update(update)
    return PlainTextResponse("ok")

@app.get("/keepalive")
async def keepalive():
    return PlainTextResponse("ok")

# ========= startup/shutdown + keepalive logs =========
async def _log_keepalive():
    while True:
        LOG.info("[keepalive] app rodando; wallet=%s", WALLET_ADDRESS)
        await asyncio.sleep(30)

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
    asyncio.create_task(_log_keepalive())

@app.on_event("shutdown")
async def on_shutdown():
    with suppress(Exception):
        await application.stop()
    with suppress(Exception):
        await application.shutdown()
