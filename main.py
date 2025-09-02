# --- imports no topo ---
import os, logging, time, asyncio
from datetime import datetime, timezone
from typing import Optional, Tuple, Dict, Any
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from telegram.error import TimedOut, TelegramError
from contextlib import suppress

# suas dependências locais
from db import (
    init_db,
    cfg_get,
    user_get_or_create,
    vip_list,
    vip_add,
    vip_remove,
    hash_exists,
    hash_store,
)

from payments import (
    resolve_payment_usd_autochain,              # já está funcionando
    WALLET_ADDRESS,                             # sua carteira destino
)
from utils import (
    choose_plan_from_usd,                       # mapeia USD -> dias
    create_one_time_invite,                     # função de convite p/ o grupo VIP
    get_prices_sync,                            # helper p/ tabela de planos
    vip_upsert_and_get_until,                   # centralizado
    make_link_sig,                              # assinatura de link compartilhada


)
# ---------- logging ----------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO),
                    format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
LOG = logging.getLogger("main")

# ---------- env ----------
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
SELF_URL = os.getenv("SELF_URL", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "secret")
GROUP_VIP_ID = int(os.getenv("GROUP_VIP_ID", "0"))
WEBAPP_URL = os.getenv("WEBAPP_URL", "")  # ex.: https://seu-servico.onrender.com/pay/
WEBAPP_LINK_SECRET = os.getenv("WEBAPP_LINK_SECRET", "change-me")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]


if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN não definido no ambiente.")

# ---------- FastAPI & static ----------
app = FastAPI()
app.mount("/pay", StaticFiles(directory="./webapp", html=True), name="pay")

# ---------- Telegram Application ----------
application = ApplicationBuilder().token(BOT_TOKEN).build()

# ---------- helpers ----------

async def prices_table() -> Dict[int, float]:
    raw = await cfg_get("vip_plan_prices_usd")
    return get_prices_sync(raw)

async def approve_by_usd_and_invite(
    tg_id: int,
    username: Optional[str],
    tx_hash: str,
    notify_user: bool = True,                          # <-- chave p/ evitar duplicidade
) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Valida a transação (auto-chain), escolhe o plano por USD e gera convite 1-uso.
    Se notify_user=False, não envia DM pelo bot (para evitar duplicidade com a página).
    """
    if await hash_exists(tx_hash):
        return False, "hash já usada", {"error": "hash_used"}
    ok, info, usd, details = await resolve_payment_usd_autochain(tx_hash)
    if not ok:
        return False, info, {"details": details}

    prices = await prices_table()
    days = choose_plan_from_usd(usd or 0.0, prices)
    if not days:
        tabela = ", ".join(f"{d}d=${p:.2f}" for d, p in sorted(prices.items()))
        return False, f"Valor em USD insuficiente (${usd:.2f}). Tabela: {tabela}", {"details": details, "usd": usd}

    until = await vip_upsert_and_get_until(tg_id, username, days)
    link = await create_one_time_invite(application.bot, GROUP_VIP_ID, expire_seconds=7200, member_limit=1)
    if not link:
        fail_msg = "Invite link creation failed, please try again later"
        if notify_user:
            with suppress(Exception):
                await application.bot.send_message(chat_id=tg_id, text=fail_msg)
        return False, fail_msg, {"error": "invite_failed", "details": details, "usd": usd, "until": until.isoformat()}

    moeda = details.get("token_symbol") or details.get("symbol") or "CRYPTO"
    msg = (
        f"Pagamento confirmado em {moeda} (${usd:.2f}).\n"
        f"Plano: {days} dias — VIP até {until.strftime('%d/%m/%Y %H:%M')}\n\n"
        f"Convite VIP (1 uso, expira em 2h):\n{link}"
    )

    if notify_user:
        with suppress(Exception):
            await application.bot.send_message(chat_id=tg_id, text=msg)

    await hash_store(tx_hash, tg_id)
    return True, msg, {"invite": link, "until": until.isoformat(), "usd": usd, "details": details}

# -------- Telegram handlers --------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await user_get_or_create(u.id, u.username)
    await update.effective_message.reply_text(
        "Bem-vindo! Passos:\n"
        "1) Abra /checkout para ver a carteira e os planos.\n"
        "2) Transfira de qualquer rede suportada para a carteira informada.\n"
        "3) Envie /tx <hash_da_transacao> (ou valide na página do checkout).\n"
        "O bot detecta a chain/moeda automaticamente e libera o VIP."
    )

async def comandos_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prices = await prices_table()
    tabela = "\n".join([f"- {d} dias: ${p:.2f}" for d, p in sorted(prices.items())])
    txt = ("Comandos:\n"
           "/checkout — ver carteira e planos\n"
           "/tx <hash> — validar pagamento pelo hash (ou use o botão no checkout)\n\n"
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

    # monta o botão webapp
    url = WEBAPP_URL or f"{SELF_URL}/pay/"
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "💳 Checkout (instruções & carteira)",
            web_app=WebAppInfo(url=f"{url}?uid={uid}&ts={ts}&sig={sig}")
        )
    ]])
    try:
        await context.bot.send_message(
            chat_id=uid,
            text="Abra o checkout para ver a carteira e validar o pagamento pelo botão.",
            reply_markup=kb,
        )
    except (TimedOut, TelegramError) as e:
        LOG.warning("Failed to send checkout message to %d: %s", uid, e)
        with suppress(Exception):
            await msg.reply_text("Falha ao enviar o checkout. Tente novamente com /checkout.")
async def tx_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.effective_message.reply_text("Uso: /tx <hash>\nEx.: /tx 0xabc...def")
    tx_hash = context.args[0].strip()
    uid = update.effective_user.id
    uname = update.effective_user.username
    ok, msg, _payload = await approve_by_usd_and_invite(uid, uname, tx_hash, notify_user=True)
    await update.effective_message.reply_text(msg)


async def vip_admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in ADMIN_IDS:
        return
    args = context.args
    if not args:
        await update.effective_message.reply_text("Uso: /vip <list|add|remove>")
        return
    sub = args[0].lower()
    if sub == "list":
        users = await vip_list()
        if not users:
            await update.effective_message.reply_text("Nenhum VIP.")
            return
        lines = []
        for u in users:
            until = u.vip_until
            until_str = until.strftime('%d/%m/%Y %H:%M') if until else '-'
            uname = f"@{u.username}" if u.username else ''
            lines.append(f"{u.tg_id} {uname} até {until_str}")
        await update.effective_message.reply_text("\n".join(lines))
    elif sub == "add":
        if len(args) < 3:
            await update.effective_message.reply_text("Uso: /vip add <tg_id> <dias>")
            return
        try:
            tgt = int(args[1])
            dias = int(args[2])
        except ValueError:
            await update.effective_message.reply_text("tg_id/dias inválidos")
            return
        if dias <= 0:
            await update.effective_message.reply_text("dias deve ser maior que zero")
            return
        until = await vip_add(tgt, dias)
        await update.effective_message.reply_text(
            f"VIP até {until.strftime('%d/%m/%Y %H:%M')}"
        )
    elif sub == "remove":
        if len(args) < 2:
            await update.effective_message.reply_text("Uso: /vip remove <tg_id>")
            return
        try:
            tgt = int(args[1])
        except ValueError:
            await update.effective_message.reply_text("tg_id inválido")
            return
        ok = await vip_remove(tgt)
        msg = "VIP removido" if ok else "Usuário não encontrado"
        await update.effective_message.reply_text(msg)
    else:
        await update.effective_message.reply_text("Uso: /vip <list|add|remove>")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Log the exception and notify admins."""
    LOG.exception("Exception while handling update", exc_info=context.error)
    for admin_id in ADMIN_IDS:
        with suppress(Exception):
            await context.bot.send_message(chat_id=admin_id, text=f"Erro: {context.error}")

application.add_handler(CommandHandler("start", start_cmd))
application.add_handler(CommandHandler("comandos", comandos_cmd))
application.add_handler(CommandHandler("checkout", checkout_cmd))
application.add_handler(CommandHandler("tx", tx_cmd))
application.add_handler(CommandHandler("vip", vip_admin_cmd))
application.add_error_handler(error_handler)

# -------- APIs para a página /pay --------

@app.get("/api/config")
async def api_config(uid: int, ts: int, sig: str):
    # valida assinatura do link
    mac = make_link_sig(WEBAPP_LINK_SECRET, uid, ts)
    if mac != sig:
        raise HTTPException(status_code=403, detail="assinatura inválida")

    prices = await prices_table()
    return JSONResponse({
        "wallet": WALLET_ADDRESS,
        "plans_usd": {str(k): v for k, v in sorted(prices.items())}
    })

@app.post("/api/validate")
async def api_validate(req: Request):
    """
    Body esperado:
    { "uid": 123, "username": "foo", "hash": "0x..." }
    Retorna { ok, message, invite?, details? }
    """
    try:
        data = await req.json()
    except Exception:
        raise HTTPException(status_code=400, detail="json inválido")

    tx_hash = (data.get("hash") or "").strip()
    uid = int(data.get("uid") or 0)
    username = data.get("username")

    if not uid or not tx_hash:
        raise HTTPException(status_code=400, detail="uid/hash obrigatórios")

    ok, msg, payload = await approve_by_usd_and_invite(uid, username, tx_hash, notify_user=False)
    return JSONResponse({"ok": ok, "message": msg, **payload})

# -------- infra util --------
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

# -------- lifecycle --------
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
    # heartbeat de log (não bloqueante)
    asyncio.create_task(_heartbeat())

async def _heartbeat():
    while True:
        await asyncio.sleep(60)
        LOG.info("[heartbeat] app ativo; wallet=%s", WALLET_ADDRESS)

@app.on_event("shutdown")
async def on_shutdown():
    with suppress(Exception):
        await application.stop()
    with suppress(Exception):
        await application.shutdown()
