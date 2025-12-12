"""
Script para criar sessão do Pyrogram localmente
Execute este script uma vez no seu computador para gerar o arquivo de sessão
"""
import os
import asyncio
from pyrogram import Client
from dotenv import load_dotenv

load_dotenv()

API_ID = os.getenv("TELEGRAM_API_ID")
API_HASH = os.getenv("TELEGRAM_API_HASH")

if not API_ID or not API_HASH:
    print("[X] ERRO: TELEGRAM_API_ID e TELEGRAM_API_HASH não encontrados no .env")
    print("[!] Obtenha em: https://my.telegram.org/apps")
    exit(1)

async def criar_sessao():
    """Cria sessão do Pyrogram"""
    print("=" * 60)
    print("CRIANDO SESSAO DO PYROGRAM")
    print("=" * 60)
    print()
    print("[*] API_ID:", API_ID)
    print("[*] API_HASH:", API_HASH[:10] + "...")
    print()
    print("[!] Você receberá um código SMS no seu Telegram")
    print("[!] Digite o código quando solicitado")
    print()

    # Nome do arquivo de sessão
    session_name = "bot_session"

    # Criar cliente Pyrogram
    app = Client(
        session_name,
        api_id=int(API_ID),
        api_hash=API_HASH,
        phone_number=None,  # Pyrogram vai pedir
        in_memory=False  # Salvar em arquivo
    )

    try:
        print("[*] Iniciando autenticação...")
        await app.start()

        me = await app.get_me()
        print()
        print("[OK] Sessão criada com sucesso!")
        print(f"[*] Usuário: {me.first_name} (@{me.username})")
        print(f"[*] ID: {me.id}")
        print()
        print(f"[OK] Arquivo de sessão criado: {session_name}.session")
        print()
        print("=" * 60)
        print("PRÓXIMOS PASSOS:")
        print("=" * 60)
        print(f"1. Encontre o arquivo '{session_name}.session' nesta pasta")
        print("2. Faça upload dele para o Render junto com o código")
        print("3. O bot usará esta sessão automaticamente")
        print()
        print("[!] IMPORTANTE: Mantenha este arquivo seguro!")
        print("[!] Ele contém credenciais de acesso à sua conta")
        print("=" * 60)

        await app.stop()

    except Exception as e:
        print()
        print(f"[X] ERRO: {e}")
        print()
        print("[*] Diagnóstico:")

        if "PHONE_NUMBER_INVALID" in str(e):
            print("   [X] Número de telefone inválido")
            print("   [!] Use o formato internacional: +5511999999999")
        elif "PHONE_CODE_INVALID" in str(e):
            print("   [X] Código SMS inválido")
            print("   [!] Digite o código exatamente como recebeu")
        elif "SESSION_PASSWORD_NEEDED" in str(e):
            print("   [!] Sua conta tem verificação em 2 fatores")
            print("   [!] Digite sua senha quando solicitado")
        else:
            print("   [!] Verifique se API_ID e API_HASH estão corretos")
            print("   [!] Obtenha em: https://my.telegram.org/apps")

if __name__ == "__main__":
    asyncio.run(criar_sessao())
