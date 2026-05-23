#!/usr/bin/env python3
"""
Script para migrar arquivos indexados do SQLite local
para o PostgreSQL de produção.

Uso:
    python migrar_para_producao.py
"""

import os
import sys
from datetime import datetime, timezone
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Fix para encoding UTF-8 no Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

load_dotenv()

print("\n" + "="*70)
print("🚀 MIGRAÇÃO DE DADOS: SQLite → PostgreSQL")
print("="*70 + "\n")

# Banco SQLite local (origem)
script_dir = os.path.dirname(os.path.abspath(__file__))
sqlite_path = os.path.join(script_dir, "bot.db")

if not os.path.exists(sqlite_path):
    print("❌ Erro: Arquivo bot.db não encontrado!")
    print(f"   Esperado em: {sqlite_path}")
    exit(1)

print(f"📂 Banco SQLite local: {sqlite_path}")
print(f"📊 Tamanho: {os.path.getsize(sqlite_path) / (1024*1024):.2f} MB\n")

# Conectar ao SQLite
sqlite_engine = create_engine(f"sqlite:///{sqlite_path}")
SQLiteSession = sessionmaker(bind=sqlite_engine)

# Obter DATABASE_URL do PostgreSQL
postgres_url = os.getenv("DATABASE_URL")

if not postgres_url or "postgresql" not in postgres_url:
    print("⚠️  DATABASE_URL não configurada ou não é PostgreSQL!")
    print("\n💡 Você pode:")
    print("   1. Pegar a URL no painel do Render/Railway onde o bot está hospedado")
    print("   2. Ou usar Supabase (https://supabase.com/)")
    print("\n📋 A URL deve ter este formato:")
    print("   postgresql://usuario:senha@host:porta/database")
    print()

    postgres_url = input("Cole aqui a DATABASE_URL do PostgreSQL de produção: ").strip()

    if not postgres_url or "postgresql" not in postgres_url:
        print("\n❌ URL inválida! Abortando.")
        exit(1)

# Limpar a URL
postgres_url = postgres_url.replace("postgres://", "postgresql://")

print(f"\n🔗 Conectando ao PostgreSQL...")
print(f"   Host: {postgres_url.split('@')[1].split('/')[0] if '@' in postgres_url else 'oculto'}")

try:
    postgres_engine = create_engine(postgres_url)
    PostgresSession = sessionmaker(bind=postgres_engine)

    # Testar conexão
    with PostgresSession() as session:
        session.execute(text("SELECT 1"))

    print("✅ Conectado ao PostgreSQL!\n")

except Exception as e:
    print(f"\n❌ Erro ao conectar no PostgreSQL: {e}")
    print("\n💡 Verifique:")
    print("   • URL está correta")
    print("   • Porta 6543 (Connection Pooler) para Supabase")
    print("   • Adicione ?sslmode=require no final se necessário")
    exit(1)

# Importar models
from main import Base, SourceFile, SentFile

print("📦 Criando tabelas no PostgreSQL (se não existirem)...")
Base.metadata.create_all(bind=postgres_engine)
print("✅ Tabelas prontas!\n")

# Iniciar migração
print("="*70)
print("🔄 INICIANDO MIGRAÇÃO")
print("="*70 + "\n")

# Contar arquivos no SQLite
with SQLiteSession() as sqlite_session:
    total_source = sqlite_session.execute(text(
        "SELECT COUNT(*) FROM source_files WHERE active = 1"
    )).scalar()

    total_sent = sqlite_session.execute(text(
        "SELECT COUNT(*) FROM sent_files"
    )).scalar()

print(f"📊 Dados a migrar:")
print(f"   • source_files: {total_source}")
print(f"   • sent_files: {total_sent}")
print()

confirmacao = input("Deseja continuar? (s/n): ").strip().lower()
if confirmacao != 's':
    print("\n⚠️  Migração cancelada pelo usuário.")
    exit(0)

print()

# Migrar source_files
print("📦 Migrando source_files...")

migrados = 0
duplicados = 0
erros = 0

with SQLiteSession() as sqlite_session:
    with PostgresSession() as postgres_session:
        # Buscar todos os arquivos ativos do SQLite
        result = sqlite_session.execute(text("""
            SELECT
                file_id, file_unique_id, file_type, message_id,
                source_chat_id, caption, file_name, file_size,
                indexed_at, active
            FROM source_files
            WHERE active = 1
            ORDER BY id
        """))

        for row in result:
            try:
                # Verificar se já existe no PostgreSQL
                existing = postgres_session.execute(text("""
                    SELECT id FROM source_files
                    WHERE file_unique_id = :file_unique_id
                """), {"file_unique_id": row[1]}).first()

                if existing:
                    duplicados += 1
                    if duplicados % 50 == 0:
                        print(f"   ⏭️  {duplicados} duplicados pulados...")
                    continue

                # Inserir no PostgreSQL (converter active para boolean)
                postgres_session.execute(text("""
                    INSERT INTO source_files (
                        file_id, file_unique_id, file_type, message_id,
                        source_chat_id, caption, file_name, file_size,
                        indexed_at, active
                    ) VALUES (
                        :file_id, :file_unique_id, :file_type, :message_id,
                        :source_chat_id, :caption, :file_name, :file_size,
                        :indexed_at, :active
                    )
                """), {
                    "file_id": row[0],
                    "file_unique_id": row[1],
                    "file_type": row[2],
                    "message_id": row[3],
                    "source_chat_id": row[4],
                    "caption": row[5],
                    "file_name": row[6],
                    "file_size": row[7],
                    "indexed_at": row[8],
                    "active": bool(row[9])  # Converter 1/0 para True/False
                })

                migrados += 1

                if migrados % 50 == 0:
                    postgres_session.commit()
                    print(f"   ✅ {migrados}/{total_source} migrados...")

            except Exception as e:
                erros += 1
                postgres_session.rollback()  # Rollback em caso de erro
                if erros <= 5:  # Mostrar apenas os primeiros 5 erros
                    print(f"   ❌ Erro: {e}")

        # Commit final
        postgres_session.commit()

print(f"\n✅ source_files concluído:")
print(f"   • Migrados: {migrados}")
print(f"   • Duplicados (pulados): {duplicados}")
print(f"   • Erros: {erros}")
print()

# Migrar sent_files (se houver)
if total_sent > 0:
    print("📤 Migrando sent_files...")

    sent_migrados = 0
    sent_duplicados = 0
    sent_erros = 0

    with SQLiteSession() as sqlite_session:
        with PostgresSession() as postgres_session:
            result = sqlite_session.execute(text("""
                SELECT
                    file_unique_id, file_type, message_id,
                    source_chat_id, sent_to_tier, sent_at, caption
                FROM sent_files
                ORDER BY id
            """))

            for row in result:
                try:
                    # Verificar se já existe
                    existing = postgres_session.execute(text("""
                        SELECT id FROM sent_files
                        WHERE file_unique_id = :file_unique_id
                        AND sent_to_tier = :sent_to_tier
                    """), {
                        "file_unique_id": row[0],
                        "sent_to_tier": row[4]
                    }).first()

                    if existing:
                        sent_duplicados += 1
                        continue

                    # Inserir
                    postgres_session.execute(text("""
                        INSERT INTO sent_files (
                            file_unique_id, file_type, message_id,
                            source_chat_id, sent_to_tier, sent_at, caption
                        ) VALUES (
                            :file_unique_id, :file_type, :message_id,
                            :source_chat_id, :sent_to_tier, :sent_at, :caption
                        )
                    """), {
                        "file_unique_id": row[0],
                        "file_type": row[1],
                        "message_id": row[2],
                        "source_chat_id": row[3],
                        "sent_to_tier": row[4],
                        "sent_at": row[5],
                        "caption": row[6]
                    })

                    sent_migrados += 1

                except Exception as e:
                    sent_erros += 1

            postgres_session.commit()

    print(f"✅ sent_files concluído:")
    print(f"   • Migrados: {sent_migrados}")
    print(f"   • Duplicados: {sent_duplicados}")
    print(f"   • Erros: {sent_erros}")
    print()

# Verificar resultado final
print("="*70)
print("🔍 VERIFICANDO MIGRAÇÃO")
print("="*70 + "\n")

with PostgresSession() as postgres_session:
    total_postgres = postgres_session.execute(text(
        "SELECT COUNT(*) FROM source_files WHERE active = true"
    )).scalar()

    total_enviados = postgres_session.execute(text(
        "SELECT COUNT(*) FROM sent_files"
    )).scalar()

    print(f"✅ PostgreSQL agora tem:")
    print(f"   • source_files: {total_postgres}")
    print(f"   • sent_files: {total_enviados}")
    print()

print("="*70)
print("🎉 MIGRAÇÃO CONCLUÍDA COM SUCESSO!")
print("="*70 + "\n")

print("💡 Próximos passos:")
print("   1. Reinicie o bot em produção")
print("   2. Use /stats_auto para verificar")
print("   3. Os envios automáticos funcionarão normalmente")
print()
