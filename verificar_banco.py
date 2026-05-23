#!/usr/bin/env python3
"""
Script para verificar quantos arquivos estão indexados no banco de dados.
"""

import os
import sys
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Fix para encoding UTF-8 no Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

load_dotenv()

# Determinar o banco de dados (mesma lógica do bot)
script_dir = os.path.dirname(os.path.abspath(__file__))
db_path = os.path.join(script_dir, "bot.db")

print("\n" + "="*70)
print("🔍 VERIFICAÇÃO DO BANCO DE DADOS")
print("="*70 + "\n")

print(f"📂 Arquivo do banco: {db_path}")
print(f"📊 Tamanho: {os.path.getsize(db_path) / (1024*1024):.2f} MB\n")

# Conectar ao banco
engine = create_engine(f"sqlite:///{db_path}")
SessionLocal = sessionmaker(bind=engine)

with SessionLocal() as session:
    # Verificar tabelas
    result = session.execute(text(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ))
    tabelas = [row[0] for row in result]

    print("📋 Tabelas encontradas:")
    for tabela in tabelas:
        print(f"   • {tabela}")
    print()

    # Verificar source_files
    if 'source_files' in tabelas:
        result = session.execute(text(
            "SELECT COUNT(*) FROM source_files WHERE active = 1"
        ))
        total_ativos = result.scalar()

        result = session.execute(text(
            "SELECT COUNT(*) FROM source_files"
        ))
        total_geral = result.scalar()

        print(f"📦 source_files (arquivos indexados):")
        print(f"   • Total: {total_geral}")
        print(f"   • Ativos: {total_ativos}")
        print(f"   • Inativos: {total_geral - total_ativos}")

        # Contar por tipo
        result = session.execute(text(
            "SELECT file_type, COUNT(*) FROM source_files WHERE active = 1 GROUP BY file_type"
        ))
        print(f"\n   Por tipo:")
        for tipo, count in result:
            print(f"      • {tipo}: {count}")

        # Mostrar alguns exemplos
        result = session.execute(text(
            "SELECT file_name, file_type, indexed_at FROM source_files WHERE active = 1 LIMIT 5"
        ))
        print(f"\n   Exemplos:")
        for nome, tipo, data in result:
            nome_curto = (nome[:50] + '...') if nome and len(nome) > 50 else (nome or 'sem nome')
            print(f"      • {nome_curto} ({tipo})")
    else:
        print("⚠️  Tabela 'source_files' não encontrada!")

    print()

    # Verificar sent_files
    if 'sent_files' in tabelas:
        result = session.execute(text(
            "SELECT COUNT(*) FROM sent_files"
        ))
        total_enviados = result.scalar()

        result = session.execute(text(
            "SELECT sent_to_tier, COUNT(*) FROM sent_files GROUP BY sent_to_tier"
        ))

        print(f"📤 sent_files (histórico de envios):")
        print(f"   • Total: {total_enviados}")
        for tier, count in result:
            print(f"   • {tier.upper()}: {count}")
    else:
        print("⚠️  Tabela 'sent_files' não encontrada!")

print("\n" + "="*70)
print("✅ Verificação concluída!")
print("="*70 + "\n")
