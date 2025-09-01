import asyncio
import logging
import secrets

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse
from telegram import Update

router = APIRouter()


@router.post("/auth_challenge")
async def auth_challenge(request: Request):
    import main

    data = await request.json()
    uid = data.get("telegram_user_id")
    if not uid:
        return JSONResponse({"ok": False, "error": "telegram_user_id é obrigatório"}, status_code=400)
    challenge = secrets.token_hex(16)
    main.LOGIN_CHALLENGES[int(uid)] = (challenge, main.now_utc())
    return {"challenge": challenge}


@router.post("/auth_verify")
async def auth_verify(request: Request):
    import main

    data = await request.json()
    uid = data.get("telegram_user_id")
    address = (data.get("address") or "").strip()
    signature = (data.get("signature") or "").strip()
    if not uid or not address or not signature:
        return JSONResponse({"ok": False, "error": "telegram_user_id, address e signature são obrigatórios"}, status_code=400)

    info = main.LOGIN_CHALLENGES.get(int(uid))
    if not info:
        return JSONResponse({"ok": False, "error": "desafio não encontrado"}, status_code=400)
    challenge, ts = info
    if main.now_utc() - ts > main.CHALLENGE_TTL:
        del main.LOGIN_CHALLENGES[int(uid)]
        return JSONResponse({"ok": False, "error": "desafio expirado"}, status_code=400)

    msg = main.encode_defunct(text=challenge)
    try:
        recovered = main.Web3().eth.account.recover_message(msg, signature=signature)
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"assinatura inválida: {e}"}, status_code=400)
    if recovered.lower() != address.lower():
        return JSONResponse({"ok": False, "error": "assinatura não confere com endereço"}, status_code=400)

    await asyncio.to_thread(main.user_address_upsert, int(uid), address)
    del main.LOGIN_CHALLENGES[int(uid)]
    return {"ok": True, "address": address}


@router.post("/crypto_webhook")
async def crypto_webhook(request: Request):
    import main

    data = await request.json()
    uid = data.get("telegram_user_id")
    tx_hash = (data.get("tx_hash") or "").strip().lower()
    amount = data.get("amount")
    chain = data.get("chain") or main.CHAIN_NAME
    symbol = data.get("symbol") or main.CHAIN_SYMBOL

    if not uid or not tx_hash:
        return JSONResponse({"ok": False, "error": "telegram_user_id e tx_hash são obrigatórios"}, status_code=400)

    try:
        res = await main.verify_tx_any(tx_hash)
        chain = res.get("chain_name", chain)
        symbol = res.get("symbol", symbol)
    except Exception as e:
        logging.exception("Erro verificando no webhook")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    approved = bool(res.get("ok"))
    if approved:
        plan_days = res.get("plan_days") or main.infer_plan_days(amount_usd=res.get("amount_usd"))
        if not plan_days:
            logging.warning("Webhook: valor da transação não corresponde a nenhum plano: %s", res.get("amount_usd"))
            approved = False
            res["reason"] = res.get("reason") or "Valor não corresponde a nenhum plano"
        amt_val = float(res.get("amount_usd") or amount or 0)
    plan = main.plan_from_amount(amt_val) or main.VipPlan.TRIMESTRAL

    with main.SessionLocal() as s:
        try:
            pay = s.query(main.Payment).filter(main.Payment.tx_hash == tx_hash).first()
            if not pay:
                pay = main.Payment(
                    user_id=int(uid),
                    tx_hash=tx_hash,
                    amount=str(res.get('amount_usd') or amount or ""),
                    chain=chain,
                    status="approved" if approved else "pending",
                    decided_at=main.now_utc() if approved else None,
                    notes=res.get("reason") if not approved else None,
                )
                s.add(pay)
            else:
                pay.status = "approved" if approved else "pending"
                pay.decided_at = main.now_utc() if approved else None
                if not approved:
                    pay.notes = res.get("reason")
            s.commit()
        except Exception:
            s.rollback()
            raise

    if not approved and (res.get("reason") or "").startswith("Transação não encontrada"):
        main.schedule_pending_tx_recheck()

    if approved:
        try:
            try:
                u = await main.application.bot.get_chat(int(uid))
                username = u.username
            except Exception:
                username = None
            main.vip_upsert_start_or_extend(int(uid), username, tx_hash, plan)
            invite_link = await main.create_and_store_personal_invite(int(uid))
            await main.application.bot.send_message(
                chat_id=int(uid),
                text=(
                    f"✅ Pagamento confirmado na rede {chain} ({symbol})!\n"
                    f"Seu VIP foi ativado por {main.PLAN_DAYS[plan]} dias.\n"
                    f"Entre no VIP: {invite_link}"
                ),
            )
        except Exception:
            logging.exception("Erro enviando invite")

    return JSONResponse({"ok": True, "verified": approved, "reason": res.get("reason")})


@router.post("/webhook")
async def telegram_webhook(request: Request):
    import main

    try:
        data = await request.json()
        update = Update.de_json(data, main.application.bot)
        await main.application.process_update(update)
    except Exception:
        logging.exception("Erro processando update Telegram")
        raise HTTPException(status_code=400, detail="Invalid update")
    return PlainTextResponse("", status_code=200)


@router.get("/")
async def root():
    import main

    return {"status": "online", "message": "Bot ready (crypto + schedules + VIP/FREE)"}


@router.get("/keepalive")
async def keepalive():
    import main

    return {"ok": True, "ts": main.now_utc().isoformat()}
