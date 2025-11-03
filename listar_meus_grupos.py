#!/usr/bin/env python3
"""
Script para listar TODOS os grupos/canais da SUA CONTA PESSOAL do Telegram.
Usa Pyrogram (User API) para acessar sua conta.

IMPORTANTE: Este script NÃO usa o bot, usa SUA CONTA PESSOAL.

Requisitos:
    pip install pyrogram tgcrypto

Você vai precisar:
    1. API ID e API HASH de my.telegram.org
    2. Número de telefone
    3. Código de verificação SMS
"""

import asyncio
import os
from pyrogram import Client

# =====================================================
# CONFIGURAÇÃO - OBTENHA EM https://my.telegram.org
# =====================================================
print("="*60)
print("🔑 CONFIGURAÇÃO DO TELEGRAM")
print("="*60)
print()
print("Para usar este script, você precisa:")
print("1. Acessar: https://my.telegram.org")
print("2. Fazer login com seu número")
print("3. Ir em 'API Development Tools'")
print("4. Criar um app (qualquer nome)")
print("5. Copiar API ID e API HASH")
print()

# Tentar carregar do arquivo ou pedir ao usuário
api_id_str = input("Digite seu API ID: ").strip()
api_hash = input("Digite seu API HASH: ").strip()

if not api_id_str or not api_hash:
    print("❌ API ID e API HASH são obrigatórios!")
    exit(1)

try:
    api_id = int(api_id_str)
except:
    print("❌ API ID deve ser um número!")
    exit(1)

print()
print("✅ Configuração OK!")
print()


async def listar_todos_meus_grupos():
    """
    Lista TODOS os grupos e canais da sua conta pessoal.
    """
    print("="*60)
    print("🔍 LISTANDO TODOS OS SEUS GRUPOS E CANAIS")
    print("="*60)
    print()

    # Criar sessão (vai pedir login na primeira vez)
    app = Client(
        name="minha_sessao",
        api_id=api_id,
        api_hash=api_hash,
        workdir="."
    )

    try:
        await app.start()

        print("✅ Login realizado com sucesso!\n")
        print("⏳ Buscando todos os seus grupos e canais...\n")

        grupos = []
        canais = []
        supergrupos = []

        # Iterar por todos os diálogos (conversas)
        async for dialog in app.get_dialogs():
            chat = dialog.chat

            # Filtrar apenas grupos e canais
            if chat.type.name == "GROUP":
                grupos.append({
                    'id': chat.id,
                    'titulo': chat.title,
                    'tipo': '👥 Grupo',
                    'username': chat.username,
                    'members': chat.members_count
                })
            elif chat.type.name == "SUPERGROUP":
                supergrupos.append({
                    'id': chat.id,
                    'titulo': chat.title,
                    'tipo': '👥 Supergrupo',
                    'username': chat.username,
                    'members': chat.members_count
                })
            elif chat.type.name == "CHANNEL":
                canais.append({
                    'id': chat.id,
                    'titulo': chat.title,
                    'tipo': '📢 Canal',
                    'username': chat.username,
                    'members': chat.members_count
                })

        # Exibir resultados
        print("="*60)
        print("📊 RESULTADOS")
        print("="*60)
        print()

        total = len(grupos) + len(supergrupos) + len(canais)
        print(f"✅ Total encontrado: {total}")
        print(f"   👥 Grupos: {len(grupos)}")
        print(f"   👥 Supergrupos: {len(supergrupos)}")
        print(f"   📢 Canais: {len(canais)}")
        print()

        # Listar grupos
        if grupos:
            print("="*60)
            print("👥 GRUPOS")
            print("="*60)
            print()
            for g in grupos:
                print(f"📝 {g['titulo']}")
                print(f"   🆔 ID: {g['id']}")
                if g['username']:
                    print(f"   🔗 @{g['username']}")
                if g['members']:
                    print(f"   👤 Membros: {g['members']}")
                print()

        # Listar supergrupos
        if supergrupos:
            print("="*60)
            print("👥 SUPERGRUPOS")
            print("="*60)
            print()
            for sg in supergrupos:
                print(f"📝 {sg['titulo']}")
                print(f"   🆔 ID: {sg['id']}")
                if sg['username']:
                    print(f"   🔗 @{sg['username']}")
                if sg['members']:
                    print(f"   👤 Membros: {sg['members']}")
                print()

        # Listar canais
        if canais:
            print("="*60)
            print("📢 CANAIS")
            print("="*60)
            print()
            for c in canais:
                print(f"📝 {c['titulo']}")
                print(f"   🆔 ID: {c['id']}")
                if c['username']:
                    print(f"   🔗 @{c['username']}")
                if c['members']:
                    print(f"   👤 Inscritos: {c['members']}")
                print()

        # Lista de IDs para copiar
        print("="*60)
        print("📋 LISTA DE IDs (COPIAR E COLAR)")
        print("="*60)
        print()

        todos = grupos + supergrupos + canais
        for item in todos:
            print(f"{item['tipo']} {item['titulo']}")
            print(f"{item['id']}")
            print()

        print("="*60)
        print("✅ Pronto!")
        print("="*60)

        await app.stop()

    except Exception as e:
        print(f"❌ Erro: {e}")
        print()
        print("Possíveis causas:")
        print("- API ID ou API HASH incorretos")
        print("- Código de verificação incorreto")
        print("- Problemas de conexão")
        try:
            await app.stop()
        except:
            pass


if __name__ == "__main__":
    try:
        asyncio.run(listar_todos_meus_grupos())
    except KeyboardInterrupt:
        print("\n\n👋 Programa interrompido pelo usuário.\n")
    except Exception as e:
        print(f"\n❌ Erro: {e}\n")
