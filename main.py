# --- imports no topo ---
import os, logging, time, asyncio, json, re
from datetime import datetime, timezone, timedelta, time as dtime
from typing import Optional, Tuple, Dict, Any
import httpx

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from telegram.error import TimedOut

from contextlib import suppress

from config import WEBAPP_URL, SELF_URL, ADMIN_IDS, OWNER_ID
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger


# suas dependências locais
from db import (
    init_db,
    cfg_get,
    cfg_set,
    user_get_or_create,
    vip_list,
    vip_add,
    vip_remove,
    hash_exists,
    hash_store,
    pack_get,
    pack_list,
    pack_get_next_vip,
    pack_get_next_free,
    pack_mark_sent,
    pack_mark_pending,
    pack_schedule,
    packs_get_due,
    scheduled_msg_create,
    scheduled_msg_list,
    scheduled_msg_get,
    scheduled_msg_update,
    scheduled_msg_toggle,
    scheduled_msg_delete,
    


)
from models import Pack

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
    send_with_retry,
    reply_with_retry,



)
from pack_flow import pack_conv_handler

# ---------- logging ----------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO),
                    format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
LOG = logging.getLogger("main")

# ---------- env ----------
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "secret")
GROUP_VIP_ID = int(os.getenv("GROUP_VIP_ID", "0"))
GROUP_FREE_ID = int(os.getenv("GROUP_FREE_ID", str(GROUP_VIP_ID)))
GROUP_FREE_ID = int(os.getenv("GROUP_FREE_ID", "0"))
WEBAPP_LINK_SECRET = os.getenv("WEBAPP_LINK_SECRET", "change-me")
PACK_VIP_TIME_KEY = "pack_vip_time"
PACK_FREE_TIME_KEY = "pack_free_time"
_packvip_event = asyncio.Event()
_packfree_event = asyncio.Event()



if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN não definido no ambiente.")

# ---------- FastAPI & static ----------
app = FastAPI()
app.mount("/pay", StaticFiles(directory="./webapp", html=True), name="pay")

# ---------- Telegram Application ----------
application = (
    ApplicationBuilder()
    .token(BOT_TOKEN)
    .connect_timeout(10.0)
    .read_timeout(10.0)
    .write_timeout(10.0)
    .build()
)
scheduler = AsyncIOScheduler()

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
            await send_with_retry(
                application.bot.send_message, chat_id=tg_id, text=fail_msg
            )
        return False, fail_msg, {"error": "invite_failed", "details": details, "usd": usd, "until": until.isoformat()}

    moeda = details.get("token_symbol") or details.get("symbol") or "CRYPTO"
    msg = (
        f"Pagamento confirmado em {moeda} (${usd:.2f}).\n"
        f"Plano: {days} dias — VIP até {until.strftime('%d/%m/%Y %H:%M')}\n\n"
        f"Convite VIP (1 uso, expira em 2h):\n{link}"
    )

    if notify_user:
        await send_with_retry(
            application.bot.send_message, chat_id=tg_id, text=msg
        )

    await hash_store(tx_hash, tg_id)
    return True, msg, {"invite": link, "until": until.isoformat(), "usd": usd, "details": details}

# -------- Telegram handlers --------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await user_get_or_create(u.id, u.username)
    await reply_with_retry(update.effective_message,
        "Bem-vindo! Passos:\n"
        "1) Abra /checkout para ver a carteira e os planos.\n"
        "2) Transfira de qualquer rede suportada para a carteira informada.\n"
        "3) Envie /tx <hash_da_transacao> (ou valide na página do checkout).\n"
        "O bot detecta a chain/moeda automaticamente e libera o VIP."
    )

async def id_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await reply_with_retry(update.effective_message, f"Seu ID: {uid}")   

async def comandos_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prices = await prices_table()
    tabela = "\n".join([f"- {d} dias: ${p:.2f}" for d, p in sorted(prices.items())])
    txt = ("Comandos:\n"
            "/id — mostrar seu ID numérico\n"
           "/checkout — ver carteira e planos\n"
           "/tx <hash> — validar pagamento pelo hash (ou use o botão no checkout)\n"
           "/pack — criar novo pack\n"
           "/admin <tg_id> — adicionar admin\n"
           "/radmin <tg_id> — remover admin\n"
           "/set_pendentevip <id> — marcar pack VIP como pendente\n"
           "/set_pendentefree <id> — marcar pack free como pendente\n"
           "/set_enviadovip <id> — marcar pack VIP como enviado\n"
           "/set_enviadofree <id> — marcar pack free como enviado\n\n"
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
    url = WEBAPP_URL
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "💳 Checkout (instruções & carteira)",
            web_app=WebAppInfo(url=f"{url}?uid={uid}&ts={ts}&sig={sig}")
        )
    ]])
    res = await send_with_retry(
        context.bot.send_message,
        chat_id=uid,
        text="Abra o checkout para ver a carteira e validar o pagamento pelo botão.",
        reply_markup=kb,
    )
    if res is None:
        with suppress(Exception):
            await reply_with_retry(msg, "Falha ao enviar o checkout. Tente novamente com /checkout.")
async def tx_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.effective_message.reply_text("Uso: /tx <hash>\nEx.: /tx 0xabc...def")
    tx_hash = context.args[0].strip()
    uid = update.effective_user.id
    uname = update.effective_user.username
    ok, msg, _payload = await approve_by_usd_and_invite(uid, uname, tx_hash, notify_user=True)
    await reply_with_retry(update.effective_message, msg)


async def packs_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    free_packs = await pack_list(False)
    vip_packs = await pack_list(True)
    def _format_pack(p: Pack) -> str:
        line = f"- {p.id}: {p.title}"
        if p.scheduled_at:
            line += f" (agendado para {p.scheduled_at.strftime('%d/%m/%Y %H:%M')})"
        return line
    sections = []
    if free_packs:
        sections.append("Packs Free:\n" + "\n".join(_format_pack(p) for p in free_packs))

    if vip_packs:
        sections.append("Packs VIP:\n" + "\n".join(_format_pack(p) for p in vip_packs))


    text = "\n\n".join(sections) if sections else "Nenhum pack disponível."
    await update.effective_message.reply_text(text)

async def _ensure_is_admin(update: Update) -> bool:
    uid = update.effective_user.id
    if uid not in ADMIN_IDS:
        await reply_with_retry(update.effective_message, "Você não tem permissão para usar este comando.")
        return False
    return True
async def _admin_add(tgt: int, msg):
    if tgt in ADMIN_IDS:
        await reply_with_retry(msg, "Usuário já é admin")
        return
    ADMIN_IDS.append(tgt)
    await cfg_set("admin_ids", ",".join(str(i) for i in ADMIN_IDS))
    await reply_with_retry(msg, "Admin adicionado")

async def _admin_remove(tgt: int, msg):
    if tgt not in ADMIN_IDS:
        await reply_with_retry(msg, "Usuário não é admin")
        return
    
    ADMIN_IDS.remove(tgt)
    await cfg_set("admin_ids", ",".join(str(i) for i in ADMIN_IDS))
    await reply_with_retry(msg, "Admin removido")


async def admin_add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _ensure_is_admin(update):
        return
    if not context.args:
        await reply_with_retry(update.effective_message, "Uso: /admin <tg_id>")
        return
    try:
        tgt = int(context.args[0])
    except ValueError:
        await reply_with_retry(update.effective_message, "tg_id inválido")
        return
    await _admin_add(tgt, update.effective_message)


async def radmin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _ensure_is_admin(update):
        return
    if not context.args:
        await reply_with_retry(update.effective_message, "Uso: /radmin <tg_id>")
        return
    try:
        tgt = int(context.args[0])
    except ValueError:
        await reply_with_retry(update.effective_message,("tg_id inválido"))
        return
    await _admin_remove(tgt, update.effective_message)

async def vip_admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in ADMIN_IDS:
        return
    args = context.args
    if not args:
        await reply_with_retry(update.effective_message,("Uso: /vip <list|add|remove>"))
        return
    sub = args[0].lower()
    if sub == "list":
        users = await vip_list()
        if not users:
            await reply_with_retry(update.effective_message,("Nenhum VIP."))
            return
        lines = []
        for u in users:
            until = u.vip_until
            until_str = until.strftime('%d/%m/%Y %H:%M') if until else '-'
            uname = f"@{u.username}" if u.username else ''
            lines.append(f"{u.tg_id} {uname} até {until_str}")
        await reply_with_retry(update.effective_message,("\n".join(lines)))
    elif sub == "add":
        if len(args) < 3:
            await reply_with_retry(update.effective_message,("Uso: /vip add <tg_id> <dias>"))
            return
        try:
            tgt = int(args[1])
            dias = int(args[2])
        except ValueError:
            await reply_with_retry(update.effective_message,("tg_id/dias inválidos"))
            return
        if dias <= 0:
            await reply_with_retry(update.effective_message,("dias deve ser maior que zero"))
            return
        until = await vip_add(tgt, dias)
        await reply_with_retry(update.effective_message,(
            f"VIP até {until.strftime('%d/%m/%Y %H:%M')}"
        )
        )
    elif sub == "remove":
        if len(args) < 2:
            await reply_with_retry(update.effective_message,("Uso: /vip remove <tg_id>"))
            return
        try:
            tgt = int(args[1])
        except ValueError:
            await reply_with_retry(update.effective_message,("tg_id inválido"))
            return
        ok = await vip_remove(tgt)
        msg = "VIP removido" if ok else "Usuário não encontrado"
        await reply_with_retry(update.effective_message,(msg))
    else:
        await reply_with_retry(update.effective_message,("Uso: /vip <list|add|remove>"))

async def pack_pending_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in ADMIN_IDS:
        return
    if not context.args:
        await reply_with_retry(update.effective_message,("Uso: /pack_pending <id>"))
        return
    try:
        pack_id = int(context.args[0])
    except ValueError:
        await reply_with_retry(update.effective_message,("ID inválido"))
        return
    ok = await pack_mark_pending(pack_id)
    msg = f"Pack {pack_id} marcado como pendente" if ok else "Pack não encontrado"
    await reply_with_retry(update.effective_message,(msg))

async def set_pendentevip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _ensure_is_admin(update):
        return
    if not context.args:
        await reply_with_retry(update.effective_message, "Uso: /set_pendentevip <id>")
        return
    try:
        pack_id = int(context.args[0])
    except ValueError:
        await reply_with_retry(update.effective_message, "ID inválido")
        return
    pack = await pack_get(pack_id)
    if not pack:
        await reply_with_retry(update.effective_message, "Pack não encontrado")
        return
    if not pack.is_vip:
        await reply_with_retry(update.effective_message, "Pack não é VIP")
        return
    await pack_mark_pending(pack_id)
    await reply_with_retry(update.effective_message, f"Pack {pack_id} marcado como pendente (VIP)")

async def set_pendentefree_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _ensure_is_admin(update):
        return
    if not context.args:
        await reply_with_retry(update.effective_message, "Uso: /set_pendentefree <id>")
        return
    try:
        pack_id = int(context.args[0])
    except ValueError:
        await reply_with_retry(update.effective_message, "ID inválido")
        return
    pack = await pack_get(pack_id)
    if not pack:
        await reply_with_retry(update.effective_message, "Pack não encontrado")
        return
    if pack.is_vip:
        await reply_with_retry(update.effective_message, "Pack não é free")
        return
    await pack_mark_pending(pack_id)
    await reply_with_retry(update.effective_message, f"Pack {pack_id} marcado como pendente (free)")

async def set_enviadovip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _ensure_is_admin(update):
        return
    if not context.args:
        await reply_with_retry(update.effective_message, "Uso: /set_enviadovip <id>")
        return
    try:
        pack_id = int(context.args[0])
    except ValueError:
        await reply_with_retry(update.effective_message, "ID inválido")
        return
    pack = await pack_get(pack_id)
    if not pack:
        await reply_with_retry(update.effective_message, "Pack não encontrado")
        return
    if not pack.is_vip:
        await reply_with_retry(update.effective_message, "Pack não é VIP")
        return
    await pack_mark_sent(pack_id)
    await reply_with_retry(update.effective_message, f"Pack {pack_id} marcado como enviado (VIP)")

async def set_enviadofree_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _ensure_is_admin(update):
        return
    if not context.args:
        await reply_with_retry(update.effective_message, "Uso: /set_enviadofree <id>")
        return
    try:
        pack_id = int(context.args[0])
    except ValueError:
        await reply_with_retry(update.effective_message, "ID inválido")
        return
    pack = await pack_get(pack_id)
    if not pack:
        await reply_with_retry(update.effective_message, "Pack não encontrado")
        return
    if pack.is_vip:
        await reply_with_retry(update.effective_message, "Pack não é free")
        return
    await pack_mark_sent(pack_id)
    await reply_with_retry(update.effective_message, f"Pack {pack_id} marcado como enviado (free)")


async def set_packvip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in ADMIN_IDS:
        return
    if not context.args:
        await reply_with_retry(update.effective_message,("Uso: /set_packvip HH:MM"))
        return
    hhmm = context.args[0]
    if not re.match(r"^\d{2}:\d{2}$", hhmm):
        await reply_with_retry(update.effective_message,("Formato inválido. Use HH:MM"))
        return
    await cfg_set(PACK_VIP_TIME_KEY, hhmm)
    _packvip_event.set()
    await reply_with_retry(update.effective_message,(f"Horário do pack VIP ajustado para {hhmm}"))

async def set_packfree_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in ADMIN_IDS:
        return
    if not context.args:
        await reply_with_retry(update.effective_message,("Uso: /set_packfree HH:MM"))
        return
    hhmm = context.args[0]
    if not re.match(r"^\d{2}:\d{2}$", hhmm):
        await reply_with_retry(update.effective_message,("Formato inválido. Use HH:MM"))
        return
    await cfg_set(PACK_FREE_TIME_KEY, hhmm)
    _packfree_event.set()
    await reply_with_retry(update.effective_message,(f"Horário do pack free ajustado para {hhmm}"))

async def schedule_pack_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in ADMIN_IDS:
        return
    if len(context.args) < 2:
        await reply_with_retry(update.effective_message,("Uso: /schedule_pack <id> <HH:MM>"))
        return
    try:
        pack_id = int(context.args[0])
    except ValueError:
        await reply_with_retry(update.effective_message,("ID inválido"))
        return
    hhmm = context.args[1]
    if not re.match(r"^\d{2}:\d{2}$", hhmm):
        await reply_with_retry(update.effective_message,("Formato inválido. Use HH:MM"))
        return
    hour, minute = map(int, hhmm.split(":"))
    now = datetime.now(timezone.utc)
    when = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if when <= now:
        when += timedelta(days=1)
    ok = await pack_schedule(pack_id, when)
    msg = (
        f"Pack {pack_id} agendado para {when.strftime('%d/%m/%Y %H:%M')}" if ok else "Pack não encontrado"
    )
    await reply_with_retry(update.effective_message,(msg))
                                              
async def send_pack_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in ADMIN_IDS:
        return
    if len(context.args) < 2:
        await reply_with_retry(update.effective_message,("Uso: /send_pack <id> <chat_id>"))
        return
    try:
        pack_id = int(context.args[0])
        chat_id = int(context.args[1])
    except ValueError:
        await reply_with_retry(update.effective_message,("IDs inválidos"))
        return
    pack = await pack_get(pack_id)
    if not pack:
        await reply_with_retry(update.effective_message,("Pack não encontrado"))
        return
    await _send_pack(pack, chat_id)
    await reply_with_retry(update.effective_message,("Pack enviado com sucesso"))

async def send_next_pack_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in ADMIN_IDS:
        return
    kind = context.args[0].lower() if context.args else "vip"
    if kind == "free":
        pack = await pack_get_next_free()
    else:
        pack = await pack_get_next_vip()
    if not pack:
        await reply_with_retry(update.effective_message,("Nenhum pack pendente"))
        return
    await _send_pack(pack)
    await reply_with_retry(update.effective_message,("Pack enviado com sucesso"))

def _parse_hhmm(hhmm: str) -> Optional[dtime]:
    if not re.match(r"^\d{2}:\d{2}$", hhmm):
        return None
    hour, minute = map(int, hhmm.split(":"))
    if 0 <= hour < 24 and 0 <= minute < 60:
        return dtime(hour=hour, minute=minute)
    return None


def schedule_sm_job(sm):
    trigger = CronTrigger(
        hour=sm.time.hour, minute=sm.time.minute, timezone=timezone.utc
    )
    scheduler.add_job(
        send_scheduled_message,
        trigger,
        args=[sm.id],
        id=f"SM#{sm.id}",
        replace_existing=True,
    )


async def send_scheduled_message(msg_id: int):
    sm = await scheduled_msg_get(msg_id)
    if not sm or not sm.enabled:
        return
    chat_id = GROUP_VIP_ID if sm.tier == "vip" else GROUP_FREE_ID
    if not chat_id:
        return
    try:
        await send_with_retry(
            application.bot.send_message, chat_id=chat_id, text=sm.text
        )
    except Exception as e:
        LOG.error("Falha ao enviar mensagem %s: %s", msg_id, e)


def _format_sm(sm) -> str:
    status = "ON" if sm.enabled else "OFF"
    preview = sm.text.replace("\n", " ")
    if len(preview) > 30:
        preview = preview[:30] + "..."
    return f"- {sm.id}: {sm.time.strftime('%H:%M')} [{status}] {preview}"


async def load_scheduled_messages():
    msgs_vip = await scheduled_msg_list("vip")
    msgs_free = await scheduled_msg_list("free")
    for sm in msgs_vip + msgs_free:
        if sm.enabled:
            schedule_sm_job(sm)


async def add_msg_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, tier: str):
    if not await _ensure_is_admin(update):
        return
    if len(context.args) < 2:
        await reply_with_retry(
            update.effective_message,
            f"Uso: /add_msg_{tier} HH:MM <texto>",
        )
        return
    hhmm = context.args[0]
    when = _parse_hhmm(hhmm)
    if not when:
        await reply_with_retry(update.effective_message, "Hora inválida. Use HH:MM")
        return
    text = " ".join(context.args[1:])
    sm = await scheduled_msg_create(tier, when, text)
    schedule_sm_job(sm)
    await reply_with_retry(
        update.effective_message,
        f"Mensagem {sm.id} agendada para {hhmm}",
    )


async def list_msgs_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, tier: str):
    if not await _ensure_is_admin(update):
        return
    msgs = await scheduled_msg_list(tier)
    if not msgs:
        await reply_with_retry(update.effective_message, "Nenhuma mensagem.")
        return
    lines = [_format_sm(m) for m in msgs]
    await reply_with_retry(update.effective_message, "\n".join(lines))


async def add_msg_vip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await add_msg_cmd(update, context, "vip")


async def add_msg_free_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await add_msg_cmd(update, context, "free")


async def list_msgs_vip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await list_msgs_cmd(update, context, "vip")


async def list_msgs_free_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await list_msgs_cmd(update, context, "free")


async def edit_msg_vip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _ensure_is_admin(update):
        return
    if not context.args:
        await reply_with_retry(
            update.effective_message, "Uso: /edit_msg_vip <id> [HH:MM] [texto]"
        )
        return
    try:
        msg_id = int(context.args[0])
    except ValueError:
        await reply_with_retry(update.effective_message, "ID inválido")
        return
    when = None
    text = None
    if len(context.args) >= 2:
        maybe_time = context.args[1]
        parsed = _parse_hhmm(maybe_time)
        if parsed:
            when = parsed
            if len(context.args) > 2:
                text = " ".join(context.args[2:])
        else:
            text = " ".join(context.args[1:])
    if when is None and text is None:
        await reply_with_retry(update.effective_message, "Nada para editar")
        return
    ok = await scheduled_msg_update(msg_id, when, text)
    if not ok:
        await reply_with_retry(update.effective_message, "Mensagem não encontrada")
        return
    sm = await scheduled_msg_get(msg_id)
    if sm and sm.enabled:
        schedule_sm_job(sm)
    await reply_with_retry(update.effective_message, "Mensagem atualizada")


async def toggle_msg_vip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _ensure_is_admin(update):
        return
    if not context.args:
        await reply_with_retry(update.effective_message, "Uso: /toggle_msg_vip <id>")
        return
    try:
        msg_id = int(context.args[0])
    except ValueError:
        await reply_with_retry(update.effective_message, "ID inválido")
        return
    state = await scheduled_msg_toggle(msg_id)
    if state is None:
        await reply_with_retry(update.effective_message, "Mensagem não encontrada")
        return
    if state:
        sm = await scheduled_msg_get(msg_id)
        if sm:
            schedule_sm_job(sm)
    else:
        with suppress(Exception):
            scheduler.remove_job(f"SM#{msg_id}")
    await reply_with_retry(
        update.effective_message,
        "Mensagem ativada" if state else "Mensagem desativada",
    )


async def del_msg_vip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _ensure_is_admin(update):
        return
    if not context.args:
        await reply_with_retry(update.effective_message, "Uso: /del_msg_vip <id>")
        return
    try:
        msg_id = int(context.args[0])
    except ValueError:
        await reply_with_retry(update.effective_message, "ID inválido")
        return
    ok = await scheduled_msg_delete(msg_id)
    if not ok:
        await reply_with_retry(update.effective_message, "Mensagem não encontrada")
        return
    with suppress(Exception):
        scheduler.remove_job(f"SM#{msg_id}")
    await reply_with_retry(update.effective_message, "Mensagem removida")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Log the exception and notify admins."""
    LOG.exception("Exception while handling update", exc_info=context.error)
    for admin_id in ADMIN_IDS:
        await send_with_retry(
            context.bot.send_message,
            chat_id=admin_id,
            text=f"Erro: {context.error}",
        )

application.add_handler(CommandHandler("start", start_cmd))
application.add_handler(CommandHandler("id", id_cmd))
application.add_handler(CommandHandler("comandos", comandos_cmd))
application.add_handler(CommandHandler("checkout", checkout_cmd))
application.add_handler(CommandHandler("tx", tx_cmd))
application.add_handler(CommandHandler("packs", packs_cmd))
application.add_handler(CommandHandler("admin", admin_add_cmd))
application.add_handler(CommandHandler("radmin", radmin_cmd))
application.add_handler(CommandHandler("add_msg_vip", add_msg_vip_cmd))
application.add_handler(CommandHandler("add_msg_free", add_msg_free_cmd))
application.add_handler(CommandHandler("list_msgs_vip", list_msgs_vip_cmd))
application.add_handler(CommandHandler("list_msgs_free", list_msgs_free_cmd))
application.add_handler(CommandHandler("edit_msg_vip", edit_msg_vip_cmd))
application.add_handler(CommandHandler("toggle_msg_vip", toggle_msg_vip_cmd))
application.add_handler(CommandHandler("del_msg_vip", del_msg_vip_cmd))
application.add_handler(CommandHandler("vip", vip_admin_cmd))
application.add_handler(CommandHandler("pack_pending", pack_pending_cmd))
application.add_handler(CommandHandler("set_pendentevip", set_pendentevip_cmd))
application.add_handler(CommandHandler("set_pendentefree", set_pendentefree_cmd))
application.add_handler(CommandHandler("set_enviadovip", set_enviadovip_cmd))
application.add_handler(CommandHandler("set_enviadofree", set_enviadofree_cmd))
application.add_handler(CommandHandler("set_packvip", set_packvip_cmd))
application.add_handler(CommandHandler("set_packfree", set_packfree_cmd))
application.add_handler(CommandHandler("schedule_pack", schedule_pack_cmd))
application.add_handler(CommandHandler("send_pack", send_pack_cmd))
application.add_handler(CommandHandler("send_next", send_next_pack_cmd))
application.add_handler(pack_conv_handler)
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

async def _send_pack(pack, chat_id: int):


    previews = json.loads(pack.previews or "[]")
    files = json.loads(pack.files or "[]")
    try:
        if previews:
            first, *rest = previews
            await send_with_retry(
                application.bot.send_photo,
                chat_id=GROUP_VIP_ID,
                photo=first,
                caption=pack.title,
            )
            for p in rest:
                await send_with_retry(
                    application.bot.send_photo, chat_id=chat_id, photo=p
                )
        else:
            await send_with_retry(
                application.bot.send_message,
                chat_id=GROUP_VIP_ID,
                text=pack.title,
            )
        for f in files:
            await send_with_retry(
                application.bot.send_document, chat_id=chat_id, document=f
            )
        await pack_mark_sent(pack.id)
    except Exception as e:
        LOG.error("Falha ao enviar pack %s: %s", pack.id, e)

async def send_vip_pack():
    pack = await pack_get_next_vip()
    if not pack:
        LOG.info("Nenhum pack VIP pendente para envio.")
        return
    await _send_pack(pack, GROUP_VIP_ID)

async def send_free_pack():
    pack = await pack_get_next_free()
    if not pack:
        LOG.info("Nenhum pack free pendente para envio.")
        return
    await _send_pack(pack, GROUP_FREE_ID)

async def packvip_loop():
    while True:
        hhmm = await cfg_get(PACK_VIP_TIME_KEY)
        if not hhmm:
            await asyncio.sleep(60)
            continue
        try:
            hour, minute = map(int, hhmm.split(":"))
        except Exception:
            LOG.error("Horário packvip inválido: %s", hhmm)
            await asyncio.sleep(60)
            continue
        now = datetime.now(timezone.utc)
        run_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if run_at <= now:
            run_at += timedelta(days=1)
        wait = (run_at - now).total_seconds()
        try:
            await asyncio.wait_for(_packvip_event.wait(), timeout=wait)
            _packvip_event.clear()
            continue
        except asyncio.TimeoutError:
            pass
        await send_vip_pack()

async def packfree_loop():
    while True:
        hhmm = await cfg_get(PACK_FREE_TIME_KEY)
        if not hhmm:
            await asyncio.sleep(60)
            continue
        try:
            hour, minute = map(int, hhmm.split(":"))
        except Exception:
            LOG.error("Horário packfree inválido: %s", hhmm)
            await asyncio.sleep(60)
            continue
        now = datetime.now(timezone.utc)
        run_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if run_at <= now:
            run_at += timedelta(days=1)
        wait = (run_at - now).total_seconds()
        try:
            await asyncio.wait_for(_packfree_event.wait(), timeout=wait)
            _packfree_event.clear()
            continue
        except asyncio.TimeoutError:
            pass
        await send_free_pack()

async def scheduled_pack_loop():
    while True:
        now = datetime.now(timezone.utc)
        packs = await packs_get_due(now)
        for pack in packs:
            chat_id = GROUP_VIP_ID if pack.is_vip else GROUP_FREE_ID
            await _send_pack(pack, chat_id)
        await asyncio.sleep(30)

# -------- lifecycle --------
@app.on_event("startup")
async def on_startup():
    LOG.info("Starting up...")
    if OWNER_ID and OWNER_ID not in ADMIN_IDS:
        ADMIN_IDS.append(OWNER_ID)
    await init_db()
    scheduler.start()
    await load_scheduled_messages()
    db_admins = await cfg_get("admin_ids")
    if db_admins:
        for s in db_admins.split(","):
            s = s.strip()
            if not s:
                continue
            try:
                i = int(s)
            except ValueError:
                LOG.warning("ID de admin inválido: %s", s)
                continue
            if i not in ADMIN_IDS:
                ADMIN_IDS.append(i)
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        LOG.info("Inicializando bot (tentativa %d/%d)...", attempt, max_attempts)
        try:
            await application.initialize()
            await application.start()
            LOG.info("Bot iniciado com sucesso na tentativa %d", attempt)
            break
        except (TimedOut, httpx.ConnectTimeout) as e:
            LOG.warning("Falha na tentativa %d de iniciar o bot: %s", attempt, e)
            if attempt >= max_attempts:
                LOG.error("Excedido limite de tentativas de inicialização do bot")
                raise
            await asyncio.sleep(2 ** attempt)
    if SELF_URL and WEBHOOK_SECRET:
        try:
            await application.bot.set_webhook(url=f"{SELF_URL}/webhook/{WEBHOOK_SECRET}")
            LOG.info("Webhook setado em %s/webhook/%s", SELF_URL, WEBHOOK_SECRET)
        except Exception as e:
            LOG.error("Falha ao setar webhook: %s", e)
    # heartbeat de log (não bloqueante)
    asyncio.create_task(_heartbeat())
    asyncio.create_task(packvip_loop())
    asyncio.create_task(packfree_loop())
    asyncio.create_task(scheduled_pack_loop())

    _packvip_event.set()
    _packfree_event.set()


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
        with suppress(Exception):
            scheduler.shutdown(wait=False)
