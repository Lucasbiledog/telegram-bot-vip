#!/usr/bin/env python3
"""
Script para transferir arquivos de um grupo Telegram para outro usando sua conta de usuário.

Usa Pyrogram (User Account) para ler mensagens do grupo fonte e transferir para o destino.
Com user account você tem acesso completo ao histórico de mensagens!

Uso:
    python transferir_arquivos_user.py

Requer:
    pip install pyrogram tgcrypto
"""

import asyncio
import os
import sys
import platform
import time

# Fix para Windows + Python 3.14+ (ANTES de importar pyrogram!)
if platform.system() == 'Windows':
    # Criar um event loop antes do pyrogram ser importado
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    except:
        pass

from datetime import datetime
from dotenv import load_dotenv
from pyrogram import Client
from pyrogram.errors import FloodWait, ChatAdminRequired, ChannelPrivate

# Carregar variáveis de ambiente do diretório pai
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(parent_dir, '.env'))

API_ID = os.getenv("TELEGRAM_API_ID")
API_HASH = os.getenv("TELEGRAM_API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")

if not API_ID or not API_HASH:
    print("❌ Erro: TELEGRAM_API_ID e TELEGRAM_API_HASH não encontrados no .env!")
    print("💡 Obtenha em: https://my.telegram.org/apps")
    sys.exit(1)


class TransferirArquivosUser:
    """Classe para transferir arquivos usando conta de usuário (Pyrogram)."""

    def __init__(self, api_id: str, api_hash: str):
        self.api_id = int(api_id)
        self.api_hash = api_hash

        # Cliente Pyrogram (user account)
        self.app = Client(
            "transferir_arquivos_session",
            api_id=self.api_id,
            api_hash=self.api_hash,
            workdir="."
        )

        # Estatísticas
        self.total_encontradas = 0
        self.total_transferidas = 0
        self.total_erros = 0
        self.tipos_encontrados = {}
        self.erros_detalhes = []

    async def iniciar(self):
        """Inicia o cliente Pyrogram."""
        await self.app.start()
        me = await self.app.get_me()
        print(f"✅ Conectado como: {me.first_name} (@{me.username or 'sem username'})")
        print(f"   ID: {me.id}\n")

    async def parar(self):
        """Para o cliente Pyrogram."""
        await self.app.stop()

    async def verificar_grupos(self, source_id: int, dest_id: int):
        """Verifica acesso aos grupos."""
        print("🔍 Verificando acesso aos grupos...\n")

        try:
            source_chat = await self.app.get_chat(source_id)
            print(f"✅ Grupo fonte: {source_chat.title}")
            print(f"   ID: {source_id}")
            print(f"   Tipo: {source_chat.type}\n")
        except ChannelPrivate:
            print(f"❌ Erro: Você não é membro do grupo fonte {source_id}")
            return False
        except Exception as e:
            print(f"❌ Erro ao acessar grupo fonte: {e}")
            return False

        try:
            dest_chat = await self.app.get_chat(dest_id)
            print(f"✅ Grupo destino: {dest_chat.title}")
            print(f"   ID: {dest_id}")
            print(f"   Tipo: {dest_chat.type}\n")

            # Verificar permissões
            try:
                member = await self.app.get_chat_member(dest_id, "me")
                print(f"   Seu status: {member.status}")
                if member.status not in ['administrator', 'creator']:
                    print(f"   ⚠️  AVISO: Você precisa ser admin para enviar mensagens!")
                print()
            except:
                pass

        except ChannelPrivate:
            print(f"❌ Erro: Você não é membro do grupo destino {dest_id}")
            return False
        except Exception as e:
            print(f"❌ Erro ao acessar grupo destino: {e}")
            return False

        return True

    async def listar_arquivos(self, chat_id: int, limit: int = None):
        """
        Lista todos os arquivos disponíveis no grupo.

        Args:
            chat_id: ID do grupo
            limit: Limite de mensagens a processar (None = sem limite)
        """
        print(f"🔍 Escaneando arquivos do grupo...\n")

        arquivos = []
        count = 0

        try:
            async for message in self.app.get_chat_history(chat_id, limit=limit or 0):
                count += 1

                if count % 100 == 0:
                    print(f"   📊 Processadas {count} mensagens... ({len(arquivos)} arquivos encontrados)")

                # Verificar se tem mídia
                tipo = None
                info = None

                if message.photo:
                    tipo = "foto"
                    info = f"ID: {message.photo.file_id}"
                elif message.video:
                    tipo = "video"
                    size_mb = message.video.file_size / (1024*1024)
                    duration = message.video.duration
                    info = f"{size_mb:.1f}MB, {duration}s"
                elif message.document:
                    tipo = "documento"
                    size_mb = message.document.file_size / (1024*1024)
                    info = f"{message.document.file_name or 'sem nome'}, {size_mb:.1f}MB"
                elif message.audio:
                    tipo = "audio"
                    duration = message.audio.duration
                    info = f"{message.audio.file_name or 'sem nome'}, {duration}s"
                elif message.animation:
                    tipo = "animacao"
                    info = "GIF/Animation"
                elif message.voice:
                    tipo = "voice"
                    duration = message.voice.duration
                    info = f"{duration}s"
                elif message.video_note:
                    tipo = "video_note"
                    info = "Vídeo circular"
                elif message.sticker:
                    tipo = "sticker"
                    info = message.sticker.emoji or "sticker"

                if tipo:
                    self.tipos_encontrados[tipo] = self.tipos_encontrados.get(tipo, 0) + 1
                    arquivos.append({
                        'message_id': message.id,
                        'tipo': tipo,
                        'info': info,
                        'caption': message.caption,
                        'date': message.date,
                        'message': message
                    })

        except Exception as e:
            print(f"❌ Erro ao escanear mensagens: {e}")

        print(f"\n✅ Scan completo! {len(arquivos)} arquivos encontrados em {count} mensagens.\n")
        return arquivos

    async def transferir_arquivo(self, message, dest_id: int):
        """
        Transfere uma mensagem com arquivo para o grupo destino.

        Args:
            message: Objeto Message do Pyrogram
            dest_id: ID do grupo destino
        """
        try:
            # Copiar mensagem preservando formatação
            await message.copy(dest_id)
            return True

        except FloodWait as e:
            print(f"   ⏸️  FloodWait: aguardando {e.value} segundos...")
            await asyncio.sleep(e.value)
            # Tentar novamente
            try:
                await message.copy(dest_id)
                return True
            except Exception as e2:
                self.erros_detalhes.append(f"Msg {message.id}: {str(e2)}")
                return False

        except ChatAdminRequired:
            print(f"   ❌ Erro: Sem permissão de admin no grupo destino")
            self.erros_detalhes.append(f"Msg {message.id}: Sem permissão de admin")
            return False

        except Exception as e:
            self.erros_detalhes.append(f"Msg {message.id}: {str(e)}")
            return False

    async def transferir_todos(self, source_id: int, dest_id: int,
                              filtro_tipo: str = None, limit: int = None,
                              delay: float = 0.5):
        """
        Transfere todos os arquivos do grupo fonte para o destino.

        Args:
            source_id: ID do grupo fonte
            dest_id: ID do grupo destino
            filtro_tipo: Tipo de arquivo para filtrar (foto, video, documento, etc)
            limit: Limite de mensagens a processar
            delay: Delay em segundos entre cada transferência
        """
        # Listar arquivos
        print("="*70)
        print("FASE 1: LISTANDO ARQUIVOS")
        print("="*70 + "\n")

        arquivos = await self.listar_arquivos(source_id, limit)

        if not arquivos:
            print("⚠️  Nenhum arquivo encontrado no grupo fonte.")
            return

        # Aplicar filtro se especificado
        if filtro_tipo:
            arquivos = [a for a in arquivos if a['tipo'] == filtro_tipo]
            print(f"🔍 Filtro aplicado: apenas '{filtro_tipo}' ({len(arquivos)} arquivos)")

        # Mostrar tipos encontrados
        print("📁 Tipos de arquivo encontrados:")
        for tipo, count in sorted(self.tipos_encontrados.items()):
            print(f"   • {tipo}: {count}")
        print()

        if not arquivos:
            print("⚠️  Nenhum arquivo corresponde ao filtro especificado.")
            return

        # Confirmar transferência
        print("="*70)
        print(f"⚠️  Serão transferidos {len(arquivos)} arquivos.")
        confirmar = input("Deseja continuar? (s/n): ").strip().lower()

        if confirmar not in ['s', 'sim', 'y', 'yes']:
            print("\n❌ Operação cancelada.\n")
            return

        # Transferir arquivos
        print("\n" + "="*70)
        print("FASE 2: TRANSFERINDO ARQUIVOS")
        print("="*70 + "\n")

        for idx, arq in enumerate(arquivos, 1):
            self.total_encontradas += 1

            tipo = arq['tipo']
            info = arq['info']
            msg_id = arq['message_id']

            print(f"[{idx}/{len(arquivos)}] Transferindo {tipo} (msg {msg_id})...")
            print(f"            {info}")

            if await self.transferir_arquivo(arq['message'], dest_id):
                self.total_transferidas += 1
                print(f"            ✅ Transferido com sucesso")
            else:
                self.total_erros += 1
                print(f"            ❌ Erro na transferência")

            print()

            # Delay para evitar flood
            if idx < len(arquivos):  # Não esperar no último
                await asyncio.sleep(delay)

    def exibir_relatorio(self):
        """Exibe relatório final."""
        print("\n" + "="*70)
        print("📊 RELATÓRIO FINAL")
        print("="*70 + "\n")

        print(f"📁 Arquivos encontrados: {self.total_encontradas}")
        print(f"✅ Transferidos com sucesso: {self.total_transferidas}")
        print(f"❌ Erros: {self.total_erros}\n")

        if self.total_transferidas > 0:
            taxa = (self.total_transferidas / self.total_encontradas * 100)
            print(f"📈 Taxa de sucesso: {taxa:.1f}%\n")

        if self.erros_detalhes and len(self.erros_detalhes) <= 10:
            print("❌ Detalhes dos erros:")
            for erro in self.erros_detalhes[:10]:
                print(f"   • {erro}")
            print()

        print("="*70)

        if self.total_transferidas > 0:
            print("✅ Transferência concluída!")
        else:
            print("⚠️  Nenhum arquivo foi transferido.")
        print()


async def main():
    """Função principal."""
    print("\n" + "="*70)
    print("📤 TRANSFERIR ARQUIVOS ENTRE GRUPOS (USER ACCOUNT)")
    print("="*70 + "\n")

    print("💡 Este script usa SUA conta do Telegram para acessar os grupos.")
    print("   Na primeira execução, será solicitado login (código SMS).\n")

    # Input dos grupos
    try:
        source_input = input("🔹 ID do grupo FONTE (onde estão os arquivos): ")
        source_id = int(source_input.strip())

        dest_input = input("🔹 ID do grupo DESTINO (para onde transferir): ")
        dest_id = int(dest_input.strip())

    except ValueError:
        print("\n❌ Erro: IDs devem ser números! Exemplo: -1003080645605\n")
        return

    # Filtro de tipo (opcional)
    print("\n🔹 Filtrar por tipo de arquivo? (opcional)")
    print("   Tipos: foto, video, documento, audio, animacao, voice, sticker")
    print("   Deixe em branco para transferir TODOS os tipos")
    filtro_tipo = input("Tipo (ou Enter para todos): ").strip().lower() or None

    # Limite de mensagens
    print("\n🔹 Quantas mensagens processar?")
    print("   Digite um número ou deixe em branco para processar TODAS")
    limit_input = input("Limite (ou Enter para todas): ").strip()
    limit = int(limit_input) if limit_input else None

    # Delay entre transferências
    print("\n🔹 Delay entre transferências (em segundos)?")
    print("   Recomendado: 0.5 a 2 segundos (evita flood)")
    delay_input = input("Delay em segundos (ou Enter para 0.5): ").strip()
    delay = float(delay_input) if delay_input else 0.5

    # Criar transferidor
    transferidor = TransferirArquivosUser(API_ID, API_HASH)

    try:
        # Iniciar cliente
        print("\n" + "="*70)
        print("🔐 CONECTANDO AO TELEGRAM")
        print("="*70 + "\n")

        await transferidor.iniciar()

        # Verificar grupos
        if not await transferidor.verificar_grupos(source_id, dest_id):
            print("\n❌ Falha na verificação dos grupos. Abortando.\n")
            return

        # Transferir arquivos
        await transferidor.transferir_todos(
            source_id,
            dest_id,
            filtro_tipo=filtro_tipo,
            limit=limit,
            delay=delay
        )

        # Exibir relatório
        transferidor.exibir_relatorio()

    except KeyboardInterrupt:
        print("\n\n⚠️  Operação interrompida pelo usuário.")
        transferidor.exibir_relatorio()

    except Exception as e:
        print(f"\n❌ Erro: {e}\n")
        import traceback
        traceback.print_exc()

    finally:
        # Parar cliente
        try:
            await transferidor.parar()
            print("👋 Desconectado.\n")
        except:
            pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n👋 Programa encerrado.\n")
    except Exception as e:
        print(f"\n❌ Erro fatal: {e}\n")
        import traceback
        traceback.print_exc()
