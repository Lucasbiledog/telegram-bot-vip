"""
Sistema Keep-Alive para manter o bot ativo 24/7 no Render (plano gratuito)
Faz auto-ping a cada 10 minutos para evitar hibernação
"""
import asyncio
import logging
import httpx
import os
from datetime import datetime

SELF_URL = os.getenv("SELF_URL", "")
PING_INTERVAL = 600  # 10 minutos em segundos

async def keep_alive_ping():
    """Faz ping no próprio serviço para mantê-lo ativo"""
    while True:
        try:
            if SELF_URL:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.get(f"{SELF_URL}/health")
                    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    if response.status_code == 200:
                        logging.info(f"✅ Keep-alive ping OK [{timestamp}]")
                    else:
                        logging.warning(f"⚠️ Keep-alive ping retornou {response.status_code} [{timestamp}]")
            else:
                logging.warning("SELF_URL não configurada - keep-alive desabilitado")
                return  # Sair do loop se não há URL configurada
        except Exception as e:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            logging.error(f"❌ Erro no keep-alive ping: {e} [{timestamp}]")

        # Aguardar intervalo antes do próximo ping
        await asyncio.sleep(PING_INTERVAL)

# Função para iniciar o keep-alive em background
def start_keep_alive_task(app):
    """Registra a task de keep-alive no startup da aplicação FastAPI"""
    @app.on_event("startup")
    async def startup_keep_alive():
        if SELF_URL:
            logging.info("🔄 Sistema Keep-Alive iniciado (ping a cada 10 minutos)")
            asyncio.create_task(keep_alive_ping())
        else:
            logging.warning("⚠️ SELF_URL não configurada - sistema keep-alive não será iniciado")
