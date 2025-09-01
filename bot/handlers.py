import asyncio
import logging
import re
from io import BytesIO
from types import SimpleNamespace
from typing import List, Optional

from telegram import Update
from telegram.ext import ContextTypes, ApplicationHandlerStop
from sqlalchemy import func

TX_RE = re.compile(r'^(0x)?[0-9a-fA-F]+$')
HASH64_RE = re.compile(r"0x[0-9a-fA-F]{64}")


def normalize_tx_hash(s: str) -> Optional[str]:
    if not s:
        return None
    s = s.strip()
    if not TX_RE.match(s):
        return None
    if s.startswith("0x"):
        return s.lower() if len(s) == 66 else None
    return ("0x" + s.lower()) if len(s) == 64 else None


def extract_tx_hashes(text: str) -> List[str]:
    if not text:
        return []
    hashes: List[str] = []
    for match in HASH64_RE.findall(text):
        h = normalize_tx_hash(match)
        if h:
            hashes.append(h)
    return hashes


async def _hashes_from_photo(photo) -> List[str]:
    try:
        file = await photo.get_file()
        buf = BytesIO()
        await file.download_to_memory(buf)
        buf.seek(0)
        try:
            import pytesseract  # type: ignore
            from PIL import Image  # type: ignore
            text = pytesseract.image_to_string(Image.open(buf))
        except Exception:
            text = ""
        return extract_tx_hashes(text)
    except Exception:
        return []


async def _hashes_from_pdf(document) -> List[str]:
    try:
        file = await document.get_file()
        buf = BytesIO()
        await file.download_to_memory(buf)
        buf.seek(0)
        try:
            from pdfminer.high_level import extract_text  # type: ignore
            text = extract_text(buf)
        except Exception:
            text = ""
        return extract_tx_hashes(text)
    except Exception:
        return []


async def auto_tx_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import main

    msg = update.effective_message
    text = (msg.text or "") + (" " + msg.caption if msg.caption else "")
    hashes = extract_tx_hashes(text)
    if not hashes:
        if getattr(msg, "photo", None):
            hashes = await _hashes_from_photo(msg.photo[-1])
        elif getattr(msg, "document", None) and msg.document.mime_type == "application/pdf":
            hashes = await _hashes_from_pdf(msg.document)
    if hashes:
        await main.tx_cmd(update, SimpleNamespace(args=[hashes[0]]))


async def tx_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import main

    msg = update.effective_message
    user = update.effective_user

    if not context.args:
        return await msg.reply_text("Uso: /tx <hash>\nEx.: /tx 0xabc123...")

    tx_hash = context.args[0].strip()
    if not tx_hash.startswith("0x"):
        tx_hash = "0x" + tx_hash
    if len(tx_hash) != 66:
        return await msg.reply_text("Hash inválida. Deve ter 64 hex (começando com 0x).")

    await msg.reply_text("🔎 Verificando a transação em múltiplas redes...")

    def _fetch_existing():
        with main.SessionLocal() as s:
            return s.query(main.Payment).filter(main.Payment.tx_hash == tx_hash).first()

    existing = await asyncio.to_thread(_fetch_existing)
    if existing and existing.user_id != user.id:
        return await msg.reply_text("Esse hash já foi usado por outro usuário.")

    if existing and existing.status == "approved":
        if existing.user_id == user.id:
            m = main.vip_get(user.id)
            try:
                invite_link = (m.invite_link if m else None) or await main.create_and_store_personal_invite(user.id)
                await main.dm(
                    user.id,
                    f"✅ Seu pagamento já estava aprovado!\n"
                    f"VIP até {m.expires_at:%d/%m/%Y} ({main.human_left(m.expires_at)}).\n"
                    f"Entre no VIP: {invite_link}",
                    parse_mode=None,
                )
                return await msg.reply_text("Esse hash já estava aprovado. Reenviei o convite no seu privado. ✅")
            except Exception as e:
                return await msg.reply_text(f"Hash aprovado, mas falhou ao reenviar o convite: {e}")
        else:
            return await msg.reply_text("Esse hash já foi usado por outro usuário.")
    elif existing and existing.status == "pending":
        return await msg.reply_text("Esse hash já foi registrado e está pendente. Aguarde a validação.")
    elif existing and existing.status == "rejected":
        return await msg.reply_text("Esse hash já foi rejeitado. Fale com um administrador.")

    try:
        res = await main.verify_tx_any(tx_hash)
    except Exception as e:
        logging.exception("Erro verificando transação")
        return await msg.reply_text(f"❌ Erro ao verificar on-chain: {e}")

    if not res or not res.get("ok"):
        reason = res.get("reason") if res else "Transação não encontrada em nenhuma cadeia."
        return await msg.reply_text(f"❌ {reason}")

    amount_usd = res.get("amount_usd") or res.get("usd")
    paid_ok = True
    plan_days = res.get("plan_days") or main.infer_plan_days(amount_usd=amount_usd)
    if not plan_days:
        logging.warning("Valor da transação não corresponde a nenhum plano: %s", amount_usd)
        paid_ok = False
        res["reason"] = res.get("reason") or "Valor não corresponde a nenhum plano"

    status = "approved" if (main.AUTO_APPROVE_CRYPTO and paid_ok) else "pending"

    sender_addr = res.get("from")
    if sender_addr:
        await asyncio.to_thread(main.user_address_upsert, user.id, sender_addr)

    def _store_payment():
        with main.SessionLocal() as s:
            try:
                p = main.Payment(
                    user_id=user.id,
                    username=user.username,
                    tx_hash=tx_hash,
                    chain=res.get("chain_name"),
                    amount=str(amount_usd or ""),
                    status=status,
                    notes=res.get("reason"),
                )
                s.add(p)
                s.commit()
            except Exception:
                s.rollback()
                raise

    try:
        await asyncio.to_thread(_store_payment)
    except Exception as e:
        logging.exception("Erro salvando pagamento")
        return await msg.reply_text(f"❌ Falha ao salvar pagamento: {e}")

    if status == "approved":
        try:
            m = main.vip_upsert_start_or_extend(user.id, user.username, tx_hash, main.plan_from_days(plan_days))
            invite_link = await main.create_and_store_personal_invite(user.id)
            await main.dm(
                user.id,
                f"✅ Pagamento confirmado!\nVIP até {m.expires_at:%d/%m/%Y} ({main.human_left(m.expires_at)}).\n"
                f"Entre no VIP: {invite_link}",
                parse_mode=None,
            )
            return await msg.reply_text("Pagamento aprovado e VIP ativado. ✅")
        except Exception as e:
            logging.exception("Erro ao ativar VIP")
            return await msg.reply_text(f"Pagamento verificado mas falhou ao ativar VIP: {e}")

    main.schedule_pending_tx_recheck()
    admin_ids = main.list_admin_ids()
    text = (
        f"🕵️ Novo pagamento pendente!\n"
        f"Usuário: {user.id} @{user.username or '-'}\n"
        f"Hash: {tx_hash}\nRede: {res.get('chain_name')}\nValor: {res.get('amount_usd') or 'N/A'} USD"
    )
    for aid in admin_ids:
        try:
            await main.dm(aid, text, parse_mode=None)
        except Exception:
            pass

    return await msg.reply_text("Pagamento registrado e aguardando aprovação manual.")


async def clear_tx_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import main

    msg = update.effective_message
    if not (update.effective_user and main.is_admin(update.effective_user.id)):
        await msg.reply_text("Apenas admins.")
        raise ApplicationHandlerStop
    if not context.args:
        return await msg.reply_text("Uso: /clear_tx <hash_da_transacao>")

    tx_raw = context.args[0]
    tx_hash = normalize_tx_hash(tx_raw)
    if not tx_hash:
        return await msg.reply_text("Hash inválida.")

    def _clear():
        with main.SessionLocal() as s:
            pay_q = s.query(main.Payment).filter(func.lower(main.Payment.tx_hash) == tx_hash)
            vm_q = s.query(main.VipMembership).filter(func.lower(main.VipMembership.tx_hash) == tx_hash)
            pays = pay_q.all()
            vms = vm_q.all()
            if not pays and not vms:
                return False
            try:
                if pays:
                    pay_q.delete(synchronize_session=False)
                    if vms:
                        vm_q.update({main.VipMembership.tx_hash: None}, synchronize_session=False)
                    s.commit()
                return True
            except Exception as e:
                s.rollback()
                raise e

    tx_hash = tx_hash.lower()
    try:
        removed = await asyncio.to_thread(_clear)
    except Exception as e:
        return await msg.reply_text(f"Erro ao remover: {e}")
    if not removed:
        return await msg.reply_text("Nenhum registro encontrado para essa hash.")
    return await msg.reply_text("Registro removido.")
