# stripe_handler.py
import os
import logging
import stripe
from fastapi import Request, HTTPException
from fastapi.responses import PlainTextResponse

from bot.telegram_webhook import bot  # para enviar mensagem no Telegram
from database import SessionLocal, Config

STRIPE_API_KEY = os.getenv("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")

stripe.api_key = STRIPE_API_KEY

async def criar_checkout_session(telegram_user_id: int):
    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": "usd",
                    "product_data": {"name": "Assinatura VIP Packs Unreal"},
                    "unit_amount": 1000,  # valor em centavos ($10.00)
                },
                "quantity": 1,
            }],
            mode="payment",
            success_url="https://seu-site.com/sucesso?session_id={CHECKOUT_SESSION_ID}",
            cancel_url="https://seu-site.com/cancelado",
            metadata={"telegram_user_id": str(telegram_user_id)},
        )
        return session.url
    except Exception as e:
        logging.error(f"Erro ao criar sessão de checkout: {e}")
        return None

def setup_stripe_webhook(app):
    @app.post("/stripe_webhook")
    async def stripe_webhook(request: Request):
        payload = await request.body()
        sig_header = request.headers.get("stripe-signature")
        try:
            event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
        except ValueError as e:
            logging.error(f"Payload inválido Stripe: {e}")
            raise HTTPException(status_code=400, detail=f"Invalid payload: {e}")
        except stripe.error.SignatureVerificationError as e:
            logging.error(f"Assinatura inválida Stripe: {e}")
            raise HTTPException(status_code=400, detail=f"Invalid signature: {e}")

        if event['type'] == 'checkout.session.completed':
            session = event['data']['object']
            telegram_user_id = session.get('metadata', {}).get('telegram_user_id')
            if telegram_user_id:
                try:
                    await bot.send_message(chat_id=int(telegram_user_id), text="✅ Pagamento confirmado! Você será adicionado ao grupo VIP.")
                    invite_link = await bot.export_chat_invite_link(chat_id=int(os.getenv("GROUP_VIP_ID")))
                    await bot.send_message(chat_id=int(telegram_user_id), text=f"Aqui está o seu link para entrar no grupo VIP:\n{invite_link}")
                    logging.info(f"Link enviado com sucesso para o usuário {telegram_user_id}")
                except Exception as e:
                    logging.error(f"Erro ao enviar link convite para grupo VIP: {e}")
            else:
                logging.warning("Webhook Stripe recebido sem telegram_user_id no metadata.")
        return PlainTextResponse("", status_code=200)
