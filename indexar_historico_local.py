#!/usr/bin/env python3
"""
Script LOCAL para indexar histórico completo do grupo.
Execute NO SEU COMPUTADOR para ler todos os arquivos antigos.
Gera um arquivo SQL que você pode importar no banco de dados.
"""

import asyncio
import os
import sys
import json
from datetime import datetime, timezone

# Fix para Python 3.14+
try:
    loop = asyncio.get_event_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

from dotenv import load_dotenv
from pyrogram import Client

load_dotenv()

API_ID = os.getenv("TELEGRAM_API_ID")
API_HASH = os.getenv("TELEGRAM_API_HASH")
SOURCE_CHAT_ID = int(os.getenv("SOURCE_CHAT_ID", "-1003080645605"))

if not API_ID or not API_HASH:
    print("\n❌ ERRO: Variáveis não encontradas no .env")
    print("\nAdicione no arquivo .env:")
    print("TELEGRAM_API_ID=seu_api_id")
    print("TELEGRAM_API_HASH=seu_api_hash")
    exit(1)


async def main():
    print("\n" + "="*70)
    print("📁 INDEXAÇÃO LOCAL DO HISTÓRICO")
    print("="*70)
    print(f"\n🔍 Grupo fonte: {SOURCE_CHAT_ID}")
    print("⏳ Isso pode demorar alguns minutos...\n")

    client = Client(
        name="indexador_local",
        api_id=int(API_ID),
        api_hash=API_HASH,
        workdir="."
    )

    arquivos = []
    stats = {
        'total': 0,
        'video': 0,
        'document': 0,
        'photo': 0,
        'audio': 0,
        'animation': 0
    }

    async with client:
        me = await client.get_me()
        print(f"👤 Conectado como: {me.first_name} (@{me.username or 'sem_username'})\n")

        try:
            chat = await client.get_chat(SOURCE_CHAT_ID)
            print(f"✅ Grupo encontrado: {chat.title}\n")
        except Exception as e:
            print(f"❌ Erro ao acessar grupo: {e}")
            print("⚠️  Certifique-se que você está no grupo!\n")
            return

        print("📨 Lendo histórico completo...\n")

        async for message in client.get_chat_history(SOURCE_CHAT_ID):
            stats['total'] += 1

            # Progress a cada 100 mensagens
            if stats['total'] % 100 == 0:
                total_files = sum([stats[k] for k in ['video', 'document', 'photo', 'audio', 'animation']])
                print(f"   📊 {stats['total']} mensagens | {total_files} arquivos encontrados")

            # Extrair arquivo
            file_data = None

            if message.photo:
                photo = message.photo
                file_data = {
                    'file_id': photo.file_id,
                    'file_unique_id': photo.file_unique_id,
                    'file_type': 'photo',
                    'file_size': photo.file_size,
                    'file_name': None
                }
                stats['photo'] += 1

            elif message.video:
                file_data = {
                    'file_id': message.video.file_id,
                    'file_unique_id': message.video.file_unique_id,
                    'file_type': 'video',
                    'file_size': message.video.file_size,
                    'file_name': message.video.file_name
                }
                stats['video'] += 1

            elif message.document:
                file_data = {
                    'file_id': message.document.file_id,
                    'file_unique_id': message.document.file_unique_id,
                    'file_type': 'document',
                    'file_size': message.document.file_size,
                    'file_name': message.document.file_name
                }
                stats['document'] += 1

            elif message.audio:
                file_data = {
                    'file_id': message.audio.file_id,
                    'file_unique_id': message.audio.file_unique_id,
                    'file_type': 'audio',
                    'file_size': message.audio.file_size,
                    'file_name': message.audio.file_name
                }
                stats['audio'] += 1

            elif message.animation:
                file_data = {
                    'file_id': message.animation.file_id,
                    'file_unique_id': message.animation.file_unique_id,
                    'file_type': 'animation',
                    'file_size': message.animation.file_size,
                    'file_name': message.animation.file_name
                }
                stats['animation'] += 1

            if file_data:
                file_data.update({
                    'message_id': message.id,
                    'source_chat_id': SOURCE_CHAT_ID,
                    'caption': message.caption,
                    'date': message.date.isoformat() if message.date else None
                })
                arquivos.append(file_data)

        # Salvar em arquivo JSON
        output_file = 'arquivos_indexados.json'
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(arquivos, f, ensure_ascii=False, indent=2)

        # Gerar SQL
        sql_file = 'import_arquivos.sql'
        with open(sql_file, 'w', encoding='utf-8') as f:
            for arq in arquivos:
                caption = arq['caption'].replace("'", "''") if arq['caption'] else None
                file_name = arq['file_name'].replace("'", "''") if arq['file_name'] else None

                sql = f"""INSERT INTO source_files (file_id, file_unique_id, file_type, message_id, source_chat_id, caption, file_name, file_size, indexed_at, active)
VALUES ('{arq['file_id']}', '{arq['file_unique_id']}', '{arq['file_type']}', {arq['message_id']}, {arq['source_chat_id']}, {'NULL' if not caption else "'" + caption + "'"}, {'NULL' if not file_name else "'" + file_name + "'"}, {arq['file_size'] or 0}, '{datetime.now(timezone.utc).isoformat()}', true)
ON CONFLICT (file_unique_id) DO NOTHING;

"""
                f.write(sql)

        # Relatório
        print("\n" + "="*70)
        print("✅ INDEXAÇÃO CONCLUÍDA!")
        print("="*70)
        print(f"\n📊 ESTATÍSTICAS:")
        print(f"   📨 Total de mensagens: {stats['total']}")
        print(f"   📁 Total de arquivos: {len(arquivos)}")
        print(f"\n📋 POR TIPO:")
        print(f"   🎥 Vídeos: {stats['video']}")
        print(f"   📄 Documents: {stats['document']}")
        print(f"   🖼️  Photos: {stats['photo']}")
        print(f"   🎵 Audios: {stats['audio']}")
        print(f"   🎬 Animations: {stats['animation']}")

        print(f"\n💾 ARQUIVOS GERADOS:")
        print(f"   1. {output_file} ({len(arquivos)} arquivos)")
        print(f"   2. {sql_file} (script SQL)")

        print(f"\n🚀 PRÓXIMOS PASSOS:")
        print(f"\n   OPÇÃO 1 - Importar via SQL (Supabase):")
        print(f"   1. Abra o Supabase Dashboard")
        print(f"   2. Vá em SQL Editor")
        print(f"   3. Cole o conteúdo de {sql_file}")
        print(f"   4. Execute")

        print(f"\n   OPÇÃO 2 - Upload JSON (Python):")
        print(f"   1. Faça upload de {output_file} para o Render")
        print(f"   2. Execute: python importar_json.py")

        print(f"\n✅ Pronto! Todos os arquivos antigos serão indexados.\n")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n❌ Cancelado pelo usuário.\n")
    except Exception as e:
        print(f"\n❌ Erro: {e}\n")
        import traceback
        traceback.print_exc()
