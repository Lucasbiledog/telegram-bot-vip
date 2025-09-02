# main.py
from __future__ import annotations

import os
import time
import hmac
import json
import asyncio
import logging
from hashlib import sha256
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

from dotenv import load_dotenv

# === Carrega .env ANTES de ler variáveis ===
load_dotenv()

# === Logging ===
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s"
)
LOG = logging.getLogger("main")

# === FastAPI app (criar ANTES de usar decorators) ===
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI()
# Servir o checkout (HTML/JS/CSS) a partir de ./webapp
app.mount("/pay", StaticFiles(directory="./webapp", html=True), name="pay")

# === Telegram PTB ===
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from contextlib import suppress

# === Integrações internas ===
from payments import resolve_payment_usd_autochain, get_wallet_address
from db import init_db, cfg_get, user_get_or_create, user_set_vip_until

# === Variáveis de ambiente ===
BOT_TOKEN       = os.getenv("BOT_TOKEN")  # obrigatória
SELF_URL        = os.getenv("SELF_URL", "").rstrip("/")
WEBHOOK_SECRET  = os.getenv("WEBHOOK_SECRET", "secret")
GROUP_VIP_ID    = int(os.getenv("GROUP_VIP_ID", "0") or "0")

WEBAPP_URL         = os.getenv("WEBAPP_URL", "")  # ex.: https://seusite.onrender.com/pay
WEBAPP_LINK_SECRET = os.getenv("WEBAPP_LINK_SECRET", "change-me")

# DEBUG/TEST flags
DEBUG_PAYMENTS  = os.getenv("DEBUG_PAYMENTS", "0") == "1"
ALLOW_ANY_TO    = os.getenv("ALLOW_ANY_TO", "0") == "1"   # payments.py também deve ler isso

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN não definido.")

# === Tabela de preços (USD) por plano (dias -> preço) ===
# Pode definir via DB (cfg_get 'vip_plan_prices_usd' em JSON) ou usamos fallback:
DEFAULT_PRICES = {30: 0.05, 60: 1.00, 180: 1.50, 365: 2.00}


def get_vip_plan_prices_usd_sync(raw: Optional[str]) -> Dict[int, float]:
    """
    Converte uma string JSON {'30': 0.05, ...} em {30: 0.05, ...}.
    """
    if not raw:
        return DEFAULT_PRICES
    try:
        data = json.loads(raw)
        parsed: Dict[int, float] = {}
        for k, v in data.items():
            try:
                parsed[int(k)] = float(v)
            except Exception:
                continue
        return parsed or DEFAULT_PRICES
    except Exception:
        return DEFAULT_PRICES


def choose_plan_from_usd(paid_usd: float, prices: Dict[int, float]) -> Optional[int]:
    """
    Escolhe o maior plano cujo preço seja <= valor pago.
    """
    candidate = None
    for days, price in prices.items():
        if paid_usd + 1e-9 >= float(price):
            if candidate is None or days > candidate:
                candidate = days
    return candidate


def normalize_tx_hash(s: str) -> Optional[str]:
    s = (s or "").strip()
    if s.startswith("0x") and len(s) == 66:
        return s.lower()
    return None


def make_link_sig(secret: str, uid: int, ts: int) -> str:
    msg = f"{uid}:{ts}".encode()
    return hmac.new(secret.encode(), msg, sha256).hexdigest()


async def prices_table() -> Dict[int, float]:
    raw = await cfg_get("vip_plan_prices_usd")
    return get_vip_plan_prices_usd_sync(raw)


async def vip_upsert_and_get_until(tg_id: int, username: Optional[str], add_days: int) -> datetime:
    """
    Lê o usuário; se já VIP, agrega dias a partir do maior entre agora e vip_until.
    Salva via user_set_vip_until e retorna a nova data.
    """
    user = await user_get_or_create(tg_id, username)
    now = datetime.now(timezone.utc)
    base = user.vip_until if (user.vip_until and user.vip_until > now) else now
    new_until = base + timedelta(days=add_days)
    await user_set_vip_until(tg_id, new_until)
    return new_until


async def create_one_time_invite(bot, chat_id: int, expire_seconds: int = 7200, member_limit: int = 1) -> str:
    """
    Cria link de convite (1 uso, expira em 2h). Requer que o bot seja admin do grupo.
    """
    expire_date = int(time.time()) + max(60, int(expire_seconds))
    link = await bot.create_chat_invite_link(
        chat_id=chat_id,
        expire_date=expire_date,
        member_limit=max(1, int(member_limit))
    )
    return link.invite_link


async def approve_by_usd_and_invite(tg_id: int, username: Optional[str], tx_hash: str) -> Tuple[bool, str]:
    """
    Resolve o pagamento (qual chain/moeda/token, valor USD), escolhe plano e envia convite.
    """
    ok, info, usd, details = await resolve_payment_usd_autochain(tx_hash)
    LOG.info("resolve_payment: ok=%s info=%s usd=%s details=%s", ok, info, usd, details)

    if not ok:
        # Erro amigável (ou detalhe completo se DEBUG)
        if DEBUG_PAYMENTS and isinstance(details, dict):
            dbg = json.dumps(details, ensure_ascii=False, indent=2)
            return False, f"{info}\n\n[DEBUG]\n{dbg}"
        return False, info

    prices = await prices_table()
    days = choose_plan_from_usd(float(usd or 0.0), prices)
    if not days:
        tabela = ", ".join(f"{d}d=${p:.2f}" for d, p in sorted(prices.items()))
        return False, f"Valor em USD insuficiente (${float(usd or 0):.2f}). Tabela: {tabela}"

    until = await vip_upsert_and_get_until(tg_id, username, days)

    if not GROUP_VIP_ID:
        # Caso não tenha grupo configurado, apenas confirma.
        moeda = (details or {}).get("token_symbol", "CRYPTO")
        msg = (
            f"Pagamento confirmado em {moeda} (${float(usd or 0):.2f}).\n"
            f"Plano: {days} dias — VIP até {until.strftime('%d/%m/%Y %H:%M')} (UTC)\n\n"
            f"*Atenção*: GROUP_VIP_ID não configurado."
        )
        return True, msg

    try:
        invite = await create_one_time_invite(context_bot, GROUP_VIP_ID, expire_seconds=7200, member_limit=1)  # preenchido depois
    except Exception as e:
        LOG.error("Falha ao criar invite link: %s", e)
        invite = None

    moeda = (details or {}).get("token_symbol", "CRYPTO")
    msg = (
        f"Pagamento confirmado em {moeda} (${float(usd or 0):.2f}).\n"
        f"Plano: {days} dias — VIP até {until.strftime('%d/%m/%Y %H:%M')} (UTC)\n\n"
        f"{'Convite VIP (1 uso, expira em 2h): ' + invite if invite else 'Não consegui gerar o link. Me avise!'}"
    )
    # DM pro usuário também (best-effort)
    with suppress(Exception):
        await application.bot.send_message(chat_id=tg_id, text=msg)

    return True, msg


# === Telegram: Handlers ===
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await user_get_or_create(u.id, u.username)
    await update.effective_message.reply_text(
        "Bem-vindo!\n\n"
        "1) Abra /checkout para ver a carteira e os planos.\n"
        "2) Transfira de QUALQUER rede suportada para a carteira exibida.\n"
        "3) Envie /tx <hash_da_transacao>.\n\n"
        "O bot detecta chain/moeda automaticamente e libera o VIP."
    )


async def comandos_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prices = await prices_table()
    tabela = "\n".join([f"- {d} dias: ${p:.2f}" for d, p in sorted(prices.items())])
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("💳 Checkout", web_app=WebAppInfo(url=WEBAPP_URL)) if WEBAPP_URL else InlineKeyboardButton("Configurar WEBAPP_URL", url="https://render.com")
    ]])
    await update.effective_message.reply_text(
        "Comandos:\n"
        "/checkout — ver carteira e planos\n"
        "/tx <hash> — validar o pagamento pelo hash\n\n"
        "Planos (USD):\n" + tabela,
        reply_markup=kb
    )


async def checkout_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    # Evita spam no grupo
    if update.effective_chat.type in ("group", "supergroup"):
        with suppress(Exception):
            await msg.delete()

    uid = update.effective_user.id
    ts = int(time.time())
    sig = make_link_sig(WEBAPP_LINK_SECRET, uid, ts)

    if not WEBAPP_URL:
        texto = "WEBAPP_URL não configurada. Envie /tx <hash> após transferir para a carteira informada."
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
        return await update.effective_message.reply_text("Hash inválido. Deve começar com 0x e ter 66 caracteres.")

    uid = update.effective_user.id
    uname = update.effective_user.username

    ok, msg = await approve_by_usd_and_invite(uid, uname, tx_hash)
    await update.effective_message.reply_text(msg)


# === Constrói Application do PTB DEPOIS de definir handlers ===
application = ApplicationBuilder().token(BOT_TOKEN).build()
application.add_handler(CommandHandler("start", start_cmd))
application.add_handler(CommandHandler("comandos", comandos_cmd))
application.add_handler(CommandHandler("checkout", checkout_cmd))
application.add_handler(CommandHandler("tx", tx_cmd))

# Para create_one_time_invite usar o bot atual
context_bot = application.bot


# === FastAPI: Webhook/validate/debug/keepalive ===
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


@app.post("/api/validate")
async def api_validate(request: Request):
    """
    Endpoint chamado pelo checkout (frontend) para validar hash e retornar
    status detalhado (inclui DEBUG quando habilitado).
    Body JSON: { "hash": "0x....", "uid": <int> (opcional) }
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid json")

    tx_hash = normalize_tx_hash((payload or {}).get("hash"))
    uid = (payload or {}).get("uid")
    uname = None

    if not tx_hash:
        return JSONResponse({"ok": False, "message": "Hash inválido."}, status_code=400)

    ok, info, usd, details = await resolve_payment_usd_autochain(tx_hash)
    LOG.info("[/api/validate] ok=%s info=%s usd=%s details=%s", ok, info, usd, details)

    resp = {
        "ok": bool(ok),
        "message": info,
        "usd": float(usd or 0.0),
        "wallet": get_wallet_address(),
        "details": details if DEBUG_PAYMENTS else {},
    }

    # Se o front quiser que já aprove e convide aqui (opcional):
    auto_approve = bool((payload or {}).get("autoApprove"))
    if ok and auto_approve and isinstance(uid, int) and uid > 0:
        ok2, msg2 = await approve_by_usd_and_invite(uid, uname, tx_hash)
        resp["auto_approved"] = ok2
        resp["auto_message"] = msg2

    return JSONResponse(resp)


@app.get("/_debug/env")
async def dbg_env():
    return {
        "SELF_URL": SELF_URL,
        "WEBHOOK_SECRET": WEBHOOK_SECRET,
        "GROUP_VIP_ID": GROUP_VIP_ID,
        "WEBAPP_URL": WEBAPP_URL,
        "WALLET_ADDRESS": get_wallet_address(),
        "DEBUG_PAYMENTS": DEBUG_PAYMENTS,
        "ALLOW_ANY_TO": ALLOW_ANY_TO,
    }


# === Startup / Shutdown ===
@app.on_event("startup")
async def on_startup():
    LOG.info("Starting up...")
    await init_db()

    # Inicializa e inicia o Application do PTB
    await application.initialize()
    with suppress(Exception):
        await application.start()

    # Configura webhook (idempotente)
    if SELF_URL and WEBHOOK_SECRET:
        try:
            await application.bot.set_webhook(url=f"{SELF_URL}/webhook/{WEBHOOK_SECRET}")
            LOG.info("Webhook setado em %s/webhook/%s", SELF_URL, WEBHOOK_SECRET)
        except Exception as e:
            LOG.error("Falha ao setar webhook: %s", e)

    # Tarefa de ‘heartbeat’ para manter logs vivos
    async def _heartbeat():
        while True:
            LOG.debug("heartbeat alive")
            await asyncio.sleep(60)
    asyncio.create_task(_heartbeat())


@app.on_event("shutdown")
async def on_shutdown():
    LOG.info("Shutting down...")
    with suppress(Exception):
        await application.stop()
    with suppress(Exception):
        await application.shutdown()
