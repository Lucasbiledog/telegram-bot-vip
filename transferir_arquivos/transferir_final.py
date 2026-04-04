#!/usr/bin/env python3
"""
Script DEFINITIVO para transferir arquivos entre grupos.

USA APENAS SUA CONTA (user) para ler E enviar.
Agrupa parts como Media Groups (álbuns).

Requisitos:
- Você precisa ser MEMBRO do grupo fonte (para ler)
- Você precisa ser MEMBRO e ADMIN do grupo destino (para enviar)
"""

import asyncio
import os
import sys
import platform
import time
import re

# Fix para Windows + Python 3.14+
if platform.system() == 'Windows':
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
from pyrogram.types import InputMediaDocument, InputMediaVideo, InputMediaAudio, InputMediaPhoto

# Carregar variáveis de ambiente
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(parent_dir, '.env'))

API_ID = os.getenv("TELEGRAM_API_ID")
API_HASH = os.getenv("TELEGRAM_API_HASH")

if not API_ID or not API_HASH:
    print("❌ Erro: TELEGRAM_API_ID e TELEGRAM_API_HASH não encontrados!")
    sys.exit(1)


class TransferirArquivosFinal:
    """Transfere arquivos usando apenas user account."""

    def __init__(self, api_id: str, api_hash: str):
        self.api_id = int(api_id)
        self.api_hash = api_hash

        # Cliente USER (para tudo)
        self.app = Client(
            "transferir_arquivos_session",  # Mesma sessão
            api_id=self.api_id,
            api_hash=self.api_hash,
            workdir="."
        )

        # Estatísticas
        self.total_encontradas = 0
        self.total_transferidas = 0
        self.total_erros = 0
        self.grupos_enviados = 0
        self.erros_detalhes = []

    async def iniciar(self):
        """Inicia o cliente."""
        await self.app.start()
        me = await self.app.get_me()
        print(f"✅ Conectado como: {me.first_name} (@{me.username or 'sem username'})")
        print(f"   ID: {me.id}\n")

    async def parar(self):
        """Para o cliente."""
        await self.app.stop()

    def extrair_nome_base(self, filename: str):
        """Extrai nome base removendo indicadores de parte."""
        if not filename:
            return None

        patterns = [
            r'[\s\-_\.](part[\s\-_]*\d+)',
            r'[\s\-_\.](parte[\s\-_]*\d+)',
            r'[\s\-_\.]p(\d+)',
            r'[\s\-_\.]\[(\d+)\]',
            r'[\s\-_\.]\((\d+)\)',
            r'\.(\d{3,})(?=\.|$)',
            r'[\s\-_\.](cd[\s\-_]*\d+)',
            r'[\s\-_\.](disc[\s\-_]*\d+)',
            r'[\s\-_\.](disk[\s\-_]*\d+)',
            r'[\s\-_\.](\d+)(?:of|de)(\d+)',
        ]

        nome_base = filename.lower()

        for pattern in patterns:
            match = re.search(pattern, nome_base, re.IGNORECASE)
            if match:
                nome_base = re.sub(pattern, '', nome_base, flags=re.IGNORECASE)
                break

        return nome_base.strip()

    def extrair_numero_parte(self, filename: str):
        """Extrai número da parte."""
        if not filename:
            return None

        patterns = [
            r'part[\s\-_]*(\d+)',
            r'parte[\s\-_]*(\d+)',
            r'[\s\-_\.]p(\d+)',
            r'\[(\d+)\]',
            r'\((\d+)\)',
            r'\.(\d{3,})(?=\.|$)',
            r'cd[\s\-_]*(\d+)',
            r'disc[\s\-_]*(\d+)',
            r'disk[\s\-_]*(\d+)',
            r'(\d+)(?:of|de)\d+',
        ]

        nome_lower = filename.lower()

        for pattern in patterns:
            match = re.search(pattern, nome_lower, re.IGNORECASE)
            if match:
                try:
                    return int(match.group(1))
                except (ValueError, IndexError):
                    continue

        return None

    def agrupar_arquivos(self, arquivos):
        """Agrupa arquivos relacionados."""
        print("🔍 Analisando e agrupando arquivos...\n")

        grupos = {}
        sem_grupo = []

        for arq in arquivos:
            nome = None
            if arq.get('message'):
                msg = arq['message']
                if msg.document:
                    nome = msg.document.file_name
                elif msg.video:
                    nome = msg.video.file_name
                elif msg.audio:
                    nome = msg.audio.file_name

            if not nome:
                sem_grupo.append(arq)
                continue

            nome_base = self.extrair_nome_base(nome)
            num_parte = self.extrair_numero_parte(nome)

            if nome_base and num_parte is not None:
                if nome_base not in grupos:
                    grupos[nome_base] = []

                arq['_nome_original'] = nome
                arq['_numero_parte'] = num_parte
                arq['_nome_base'] = nome_base
                grupos[nome_base].append(arq)
            else:
                sem_grupo.append(arq)

        # Ordenar por número da parte
        for nome_base in grupos:
            grupos[nome_base].sort(key=lambda x: x['_numero_parte'])

        total_em_grupos = sum(len(g) for g in grupos.values())

        print(f"📊 Análise:")
        print(f"   • {len(grupos)} grupos de parts encontrados")
        print(f"   • {total_em_grupos} arquivos em grupos (enviados como álbum)")
        print(f"   • {len(sem_grupo)} arquivos individuais\n")

        if grupos:
            print("📁 Grupos que serão enviados como ÁLBUM:")
            for idx, (nome_base, arquivos_grupo) in enumerate(sorted(grupos.items()), 1):
                print(f"   {idx}. {nome_base}")
                print(f"      └─ {len(arquivos_grupo)} partes (Part {arquivos_grupo[0]['_numero_parte']} até Part {arquivos_grupo[-1]['_numero_parte']})")

        print()

        resultado = []
        for nome_base in sorted(grupos.keys()):
            resultado.append((True, grupos[nome_base]))
        for arq in sem_grupo:
            resultado.append((False, [arq]))

        return resultado

    async def resolver_peer(self, chat_id: int):
        """Resolve peer buscando nos diálogos."""
        try:
            async for dialog in self.app.get_dialogs():
                if dialog.chat.id == chat_id:
                    return True
            return False
        except:
            return False

    async def verificar_grupos(self, source_id: int, dest_id: int):
        """Verifica acesso aos grupos."""
        print("🔍 Verificando acesso aos grupos...\n")

        # Verificar grupo fonte
        print("📥 Grupo FONTE (leitura):")
        await self.resolver_peer(source_id)

        try:
            source_chat = await self.app.get_chat(source_id)
            print(f"   ✅ {source_chat.title}")
            print(f"   🆔 {source_id}\n")
        except Exception as e:
            print(f"   ❌ Erro: {e}")
            print(f"   💡 Você precisa ser MEMBRO deste grupo!\n")
            return False

        # Verificar grupo destino
        print("📤 Grupo DESTINO (envio):")
        await self.resolver_peer(dest_id)

        try:
            dest_chat = await self.app.get_chat(dest_id)
            print(f"   ✅ {dest_chat.title}")
            print(f"   🆔 {dest_id}")

            # Verificar se é admin
            try:
                member = await self.app.get_chat_member(dest_id, "me")
                print(f"   👤 Seu status: {member.status}")

                if member.status not in ['administrator', 'creator']:
                    print(f"   ⚠️  AVISO: Você precisa ser ADMIN para enviar arquivos!")
                    print(f"   Tente mesmo assim? (pode dar erro)")
            except:
                pass

            print()

        except Exception as e:
            print(f"   ❌ Erro: {e}")
            print(f"   💡 Você precisa ser MEMBRO e ADMIN deste grupo!\n")
            return False

        return True

    async def listar_arquivos(self, chat_id: int, limit: int = None):
        """Lista arquivos do grupo."""
        print(f"🔍 Escaneando arquivos do grupo...\n")

        arquivos = []
        count = 0

        try:
            async for message in self.app.get_chat_history(chat_id, limit=limit or 0):
                count += 1

                if count % 100 == 0:
                    print(f"   📊 {count} mensagens... ({len(arquivos)} arquivos)")

                if message.video or message.document or message.audio or message.photo:
                    tipo = None
                    info = None

                    if message.video:
                        tipo = "video"
                        nome = message.video.file_name or "video.mp4"
                        size_mb = message.video.file_size / (1024*1024)
                        info = f"{nome}, {size_mb:.1f}MB"
                    elif message.document:
                        tipo = "documento"
                        nome = message.document.file_name or "documento"
                        size_mb = message.document.file_size / (1024*1024)
                        info = f"{nome}, {size_mb:.1f}MB"
                    elif message.audio:
                        tipo = "audio"
                        nome = message.audio.file_name or "audio.mp3"
                        info = f"{nome}"
                    elif message.photo:
                        tipo = "foto"
                        info = "foto"

                    if tipo:
                        arquivos.append({
                            'message_id': message.id,
                            'tipo': tipo,
                            'info': info,
                            'message': message
                        })

        except Exception as e:
            print(f"❌ Erro: {e}")

        print(f"\n✅ {len(arquivos)} arquivos encontrados.\n")
        return arquivos

    async def enviar_media_group(self, mensagens, dest_id: int, nome_grupo: str):
        """
        Envia múltiplas mensagens como Media Group (álbum).
        Limite: 10 arquivos por grupo (Telegram).
        """
        try:
            MAX_POR_GRUPO = 10

            if len(mensagens) > MAX_POR_GRUPO:
                print(f"   ⚠️  {len(mensagens)} partes. Dividindo em lotes de {MAX_POR_GRUPO}.")

            for i in range(0, len(mensagens), MAX_POR_GRUPO):
                lote = mensagens[i:i + MAX_POR_GRUPO]
                media_list = []

                for msg in lote:
                    message = msg['message']
                    num_parte = msg.get('_numero_parte', '?')
                    caption = f"📦 {nome_grupo} - Part {num_parte}"

                    # Usar file_id (evita re-upload)
                    if message.video:
                        media_list.append(
                            InputMediaVideo(
                                media=message.video.file_id,
                                caption=caption
                            )
                        )
                    elif message.document:
                        media_list.append(
                            InputMediaDocument(
                                media=message.document.file_id,
                                caption=caption
                            )
                        )
                    elif message.audio:
                        media_list.append(
                            InputMediaAudio(
                                media=message.audio.file_id,
                                caption=caption
                            )
                        )
                    elif message.photo:
                        media_list.append(
                            InputMediaPhoto(
                                media=message.photo.file_id,
                                caption=caption
                            )
                        )

                if media_list:
                    # Enviar como media group
                    await self.app.send_media_group(
                        chat_id=dest_id,
                        media=media_list
                    )

                    if len(mensagens) > MAX_POR_GRUPO:
                        lote_num = (i // MAX_POR_GRUPO) + 1
                        total_lotes = (len(mensagens) + MAX_POR_GRUPO - 1) // MAX_POR_GRUPO
                        print(f"   ✅ Lote {lote_num}/{total_lotes} enviado ({len(media_list)} arquivos no álbum)")
                    else:
                        print(f"   ✅ Álbum enviado ({len(media_list)} arquivos agrupados)")

                    if i + MAX_POR_GRUPO < len(mensagens):
                        await asyncio.sleep(1)

            return True

        except FloodWait as e:
            print(f"   ⏸️  FloodWait: aguardando {e.value}s...")
            await asyncio.sleep(e.value)
            return False
        except ChatAdminRequired:
            print(f"   ❌ Erro: Você não tem permissão de admin!")
            return False
        except Exception as e:
            print(f"   ❌ Erro ao enviar álbum: {e}")
            self.erros_detalhes.append(str(e))
            return False

    async def enviar_arquivo_individual(self, message, dest_id: int):
        """Envia arquivo individual."""
        try:
            await message.copy(dest_id)
            return True
        except FloodWait as e:
            print(f"   ⏸️  FloodWait: {e.value}s...")
            await asyncio.sleep(e.value)
            try:
                await message.copy(dest_id)
                return True
            except:
                return False
        except ChatAdminRequired:
            print(f"   ❌ Sem permissão de admin!")
            return False
        except Exception as e:
            return False

    async def transferir_todos(self, source_id: int, dest_id: int,
                              limit: int = None, delay: float = 1.0):
        """Transfere todos arquivos."""

        print("="*70)
        print("FASE 1: ESCANEANDO")
        print("="*70 + "\n")

        arquivos = await self.listar_arquivos(source_id, limit)
        if not arquivos:
            print("⚠️  Nenhum arquivo encontrado.")
            return

        print("="*70)
        print("FASE 2: AGRUPANDO PARTS")
        print("="*70 + "\n")

        grupos = self.agrupar_arquivos(arquivos)

        total_arquivos = sum(len(g[1]) for g in grupos)
        total_albuns = len([g for g in grupos if g[0] and len(g[1]) > 1])
        total_individuais = len([g for g in grupos if not g[0] or len(g[1]) == 1])

        print("="*70)
        print(f"📊 RESUMO DA TRANSFERÊNCIA")
        print(f"   📦 {total_albuns} álbuns (parts agrupadas)")
        print(f"   📄 {total_individuais} arquivos individuais")
        print(f"   📁 Total: {total_arquivos} arquivos")
        print("="*70)

        confirmar = input("\n▶️  Iniciar transferência? (s/n): ").strip().lower()
        if confirmar not in ['s', 'sim', 'y', 'yes']:
            print("\n❌ Cancelado.\n")
            return

        print("\n" + "="*70)
        print("FASE 3: TRANSFERINDO")
        print("="*70 + "\n")

        contador = 0

        for é_grupo, arquivos_grupo in grupos:
            if é_grupo and len(arquivos_grupo) > 1:
                # Enviar como álbum
                nome_base = arquivos_grupo[0].get('_nome_base', 'grupo')
                contador += 1

                print(f"\n📦 [{contador}/{len(grupos)}] ÁLBUM: {nome_base}")
                print(f"   Parts: {len(arquivos_grupo)} ({arquivos_grupo[0]['_numero_parte']} até {arquivos_grupo[-1]['_numero_parte']})")

                if await self.enviar_media_group(arquivos_grupo, dest_id, nome_base):
                    self.grupos_enviados += 1
                    self.total_transferidas += len(arquivos_grupo)
                else:
                    self.total_erros += len(arquivos_grupo)

                await asyncio.sleep(delay)

            else:
                # Enviar individual
                for arq in arquivos_grupo:
                    contador += 1
                    self.total_encontradas += 1

                    print(f"\n📄 [{contador}/{len(grupos)}] Individual: {arq['tipo']}")
                    print(f"   {arq['info']}")

                    if await self.enviar_arquivo_individual(arq['message'], dest_id):
                        self.total_transferidas += 1
                        print(f"   ✅ Enviado")
                    else:
                        self.total_erros += 1
                        print(f"   ❌ Erro")

                    await asyncio.sleep(delay)

    def exibir_relatorio(self):
        """Exibe relatório final."""
        print("\n" + "="*70)
        print("📊 RELATÓRIO FINAL")
        print("="*70 + "\n")

        print(f"📦 Álbuns enviados: {self.grupos_enviados}")
        print(f"✅ Arquivos transferidos: {self.total_transferidas}")
        print(f"❌ Erros: {self.total_erros}\n")

        if self.total_transferidas > 0:
            print("="*70)
            print("✅ Transferência concluída com sucesso!")
        else:
            print("="*70)
            print("⚠️  Nenhum arquivo foi transferido.")

        print()


async def main():
    """Função principal."""
    print("\n" + "="*70)
    print("📦 TRANSFERIR ARQUIVOS COM ÁLBUNS (Parts agrupadas)")
    print("="*70 + "\n")

    print("💡 Parts são agrupadas visualmente como álbum!")
    print("   Usa sua conta para ler E enviar.\n")

    try:
        fonte_input = input("🔹 ID grupo FONTE: ").strip()
        fonte_id = int(fonte_input)

        dest_input = input("🔹 ID grupo DESTINO: ").strip()
        dest_id = int(dest_input)
    except ValueError:
        print("\n❌ ID inválido!\n")
        return

    limit_input = input("\n🔹 Limite de mensagens (Enter = todas): ").strip()
    limit = int(limit_input) if limit_input else None

    delay_input = input("🔹 Delay entre envios em segundos (Enter = 1): ").strip()
    delay = float(delay_input) if delay_input else 1.0

    transferidor = TransferirArquivosFinal(API_ID, API_HASH)

    try:
        print("\n" + "="*70)
        print("🔐 CONECTANDO")
        print("="*70 + "\n")

        await transferidor.iniciar()

        if not await transferidor.verificar_grupos(fonte_id, dest_id):
            print("❌ Falha na verificação. Verifique os requisitos acima.\n")
            return

        await transferidor.transferir_todos(fonte_id, dest_id, limit=limit, delay=delay)
        transferidor.exibir_relatorio()

    except KeyboardInterrupt:
        print("\n\n⚠️  Interrompido pelo usuário.")
        transferidor.exibir_relatorio()
    except Exception as e:
        print(f"\n❌ Erro: {e}\n")
        import traceback
        traceback.print_exc()
    finally:
        try:
            await transferidor.parar()
            print("👋 Desconectado.\n")
        except:
            pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n👋 Encerrado.\n")
