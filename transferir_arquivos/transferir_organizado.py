#!/usr/bin/env python3
"""
Script para transferir arquivos mantendo organização de parts/partes.

Arquivos com Part 1, Part 2, etc são agrupados e enviados em sequência.
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

# Carregar variáveis de ambiente
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(parent_dir, '.env'))

API_ID = os.getenv("TELEGRAM_API_ID")
API_HASH = os.getenv("TELEGRAM_API_HASH")

if not API_ID or not API_HASH:
    print("❌ Erro: TELEGRAM_API_ID e TELEGRAM_API_HASH não encontrados!")
    sys.exit(1)


class TransferirArquivosOrganizado:
    """Classe para transferir arquivos mantendo organização."""

    def __init__(self, api_id: str, api_hash: str):
        self.api_id = int(api_id)
        self.api_hash = api_hash
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
        self.grupos_encontrados = 0
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

    def extrair_nome_base(self, filename: str):
        """
        Extrai o nome base de um arquivo removendo indicadores de parte.

        Detecta patterns como:
        - Part 1, Part 2, Part 3
        - Parte 1, Parte 2
        - part1, part2
        - p1, p2, p3
        - [01], [02], [03]
        - (1), (2), (3)
        - .001, .002, .003
        - CD1, CD2, CD3
        - Disc 1, Disc 2
        """
        if not filename:
            return None

        # Patterns de partes (do mais específico ao mais geral)
        patterns = [
            r'[\s\-_\.](part[\s\-_]*\d+)',  # Part 1, part-1, part_1
            r'[\s\-_\.](parte[\s\-_]*\d+)',  # Parte 1, parte-1
            r'[\s\-_\.]p(\d+)',  # p1, p2
            r'[\s\-_\.]\[(\d+)\]',  # [01], [02]
            r'[\s\-_\.]\((\d+)\)',  # (1), (2)
            r'\.(\d{3,})(?=\.|$)',  # .001, .002
            r'[\s\-_\.](cd[\s\-_]*\d+)',  # CD1, CD2
            r'[\s\-_\.](disc[\s\-_]*\d+)',  # Disc 1, Disc 2
            r'[\s\-_\.](disk[\s\-_]*\d+)',  # Disk 1, Disk 2
            r'[\s\-_\.](\d+)(?:of|de)(\d+)',  # 1of3, 1de3
        ]

        nome_base = filename.lower()

        for pattern in patterns:
            match = re.search(pattern, nome_base, re.IGNORECASE)
            if match:
                # Remove a parte encontrada
                nome_base = re.sub(pattern, '', nome_base, flags=re.IGNORECASE)
                break

        return nome_base.strip()

    def extrair_numero_parte(self, filename: str):
        """
        Extrai o número da parte de um arquivo.
        Retorna None se não encontrar.
        """
        if not filename:
            return None

        # Patterns para extrair número
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
        """
        Agrupa arquivos relacionados (parts, partes, etc).
        Retorna lista de grupos ordenados.
        """
        print("🔍 Analisando e agrupando arquivos relacionados...\n")

        # Dicionário: nome_base -> lista de arquivos
        grupos = {}
        sem_grupo = []

        for arq in arquivos:
            # Pegar nome do arquivo
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
                # Se não tem nome, adiciona aos sem grupo
                sem_grupo.append(arq)
                continue

            # Extrair nome base e número da parte
            nome_base = self.extrair_nome_base(nome)
            num_parte = self.extrair_numero_parte(nome)

            if nome_base and num_parte is not None:
                # Arquivo tem partes - adicionar ao grupo
                if nome_base not in grupos:
                    grupos[nome_base] = []

                arq['_nome_original'] = nome
                arq['_numero_parte'] = num_parte
                arq['_nome_base'] = nome_base
                grupos[nome_base].append(arq)
            else:
                # Arquivo não tem indicador de parte
                sem_grupo.append(arq)

        # Ordenar arquivos dentro de cada grupo por número da parte
        for nome_base in grupos:
            grupos[nome_base].sort(key=lambda x: x['_numero_parte'])

        # Estatísticas
        self.grupos_encontrados = len(grupos)
        total_em_grupos = sum(len(g) for g in grupos.values())

        print(f"📊 Análise concluída:")
        print(f"   • {self.grupos_encontrados} grupos de arquivos relacionados encontrados")
        print(f"   • {total_em_grupos} arquivos em grupos (serão enviados juntos)")
        print(f"   • {len(sem_grupo)} arquivos individuais\n")

        if grupos:
            print("📁 Grupos encontrados:")
            for idx, (nome_base, arquivos_grupo) in enumerate(grupos.items(), 1):
                print(f"\n   {idx}. {nome_base} ({len(arquivos_grupo)} partes)")
                for arq in arquivos_grupo:
                    print(f"      └─ Part {arq['_numero_parte']}: {arq['_nome_original']}")

        print()

        # Retornar: lista de (é_grupo, arquivos)
        resultado = []

        # Adicionar grupos ordenados
        for nome_base in sorted(grupos.keys()):
            resultado.append((True, grupos[nome_base]))

        # Adicionar arquivos individuais
        for arq in sem_grupo:
            resultado.append((False, [arq]))

        return resultado

    async def verificar_grupos(self, source_id: int, dest_id: int):
        """Verifica acesso aos grupos."""
        print("🔍 Verificando acesso aos grupos...\n")

        try:
            source_chat = await self.app.get_chat(source_id)
            print(f"✅ Grupo fonte: {source_chat.title}")
            print(f"   ID: {source_id}\n")
        except Exception as e:
            print(f"❌ Erro ao acessar grupo fonte: {e}")
            return False

        try:
            dest_chat = await self.app.get_chat(dest_id)
            print(f"✅ Grupo destino: {dest_chat.title}")
            print(f"   ID: {dest_id}\n")
        except Exception as e:
            print(f"❌ Erro ao acessar grupo destino: {e}")
            return False

        return True

    async def listar_arquivos(self, chat_id: int, limit: int = None):
        """Lista arquivos com mídia do grupo."""
        print(f"🔍 Escaneando arquivos do grupo...\n")

        arquivos = []
        count = 0

        try:
            async for message in self.app.get_chat_history(chat_id, limit=limit or 0):
                count += 1

                if count % 100 == 0:
                    print(f"   📊 Processadas {count} mensagens... ({len(arquivos)} arquivos encontrados)")

                # Verificar se tem mídia
                if message.video or message.document or message.audio:
                    tipo = None
                    info = None

                    if message.video:
                        tipo = "video"
                        size_mb = message.video.file_size / (1024*1024)
                        nome = message.video.file_name or "sem nome"
                        info = f"{nome}, {size_mb:.1f}MB"
                    elif message.document:
                        tipo = "documento"
                        size_mb = message.document.file_size / (1024*1024)
                        nome = message.document.file_name or "sem nome"
                        info = f"{nome}, {size_mb:.1f}MB"
                    elif message.audio:
                        tipo = "audio"
                        nome = message.audio.file_name or "sem nome"
                        info = f"{nome}"

                    if tipo:
                        arquivos.append({
                            'message_id': message.id,
                            'tipo': tipo,
                            'info': info,
                            'message': message
                        })

        except Exception as e:
            print(f"❌ Erro ao escanear: {e}")

        print(f"\n✅ Scan completo! {len(arquivos)} arquivos encontrados.\n")
        return arquivos

    async def transferir_arquivo(self, message, dest_id: int):
        """Transfere uma mensagem."""
        try:
            await message.copy(dest_id)
            return True
        except FloodWait as e:
            print(f"   ⏸️  FloodWait: aguardando {e.value}s...")
            await asyncio.sleep(e.value)
            try:
                await message.copy(dest_id)
                return True
            except Exception as e2:
                return False
        except Exception as e:
            return False

    async def transferir_todos(self, source_id: int, dest_id: int,
                              limit: int = None, delay: float = 0.5,
                              delay_entre_grupos: float = 2.0):
        """Transfere todos os arquivos mantendo organização."""

        # Listar arquivos
        print("="*70)
        print("FASE 1: ESCANEANDO ARQUIVOS")
        print("="*70 + "\n")

        arquivos = await self.listar_arquivos(source_id, limit)

        if not arquivos:
            print("⚠️  Nenhum arquivo encontrado.")
            return

        # Agrupar arquivos
        print("="*70)
        print("FASE 2: AGRUPANDO ARQUIVOS RELACIONADOS")
        print("="*70 + "\n")

        grupos = self.agrupar_arquivos(arquivos)

        # Confirmar
        total_arquivos = sum(len(g[1]) for g in grupos)
        print("="*70)
        print(f"⚠️  Serão transferidos {total_arquivos} arquivos em {len(grupos)} grupos.")
        print(f"   Delay entre arquivos: {delay}s")
        print(f"   Delay entre grupos: {delay_entre_grupos}s")
        confirmar = input("\nDeseja continuar? (s/n): ").strip().lower()

        if confirmar not in ['s', 'sim']:
            print("\n❌ Operação cancelada.\n")
            return

        # Transferir
        print("\n" + "="*70)
        print("FASE 3: TRANSFERINDO ARQUIVOS")
        print("="*70 + "\n")

        contador_global = 0

        for idx_grupo, (é_grupo, arquivos_grupo) in enumerate(grupos, 1):

            if é_grupo and len(arquivos_grupo) > 1:
                # É um grupo de partes
                nome_base = arquivos_grupo[0].get('_nome_base', 'desconhecido')
                print(f"\n{'='*70}")
                print(f"📦 GRUPO {idx_grupo}/{len(grupos)}: {nome_base}")
                print(f"   {len(arquivos_grupo)} partes relacionadas")
                print(f"{'='*70}\n")

            for arq in arquivos_grupo:
                contador_global += 1
                self.total_encontradas += 1

                tipo = arq['tipo']
                info = arq['info']
                msg_id = arq['message_id']

                if é_grupo and len(arquivos_grupo) > 1:
                    num_parte = arq.get('_numero_parte', '?')
                    print(f"   [{contador_global}/{total_arquivos}] Part {num_parte} - {tipo}")
                else:
                    print(f"[{contador_global}/{total_arquivos}] {tipo}")

                print(f"            {info}")

                if await self.transferir_arquivo(arq['message'], dest_id):
                    self.total_transferidas += 1
                    print(f"            ✅ Transferido")
                else:
                    self.total_erros += 1
                    print(f"            ❌ Erro")

                # Delay entre arquivos
                if contador_global < total_arquivos:
                    await asyncio.sleep(delay)

            # Delay extra entre grupos
            if é_grupo and len(arquivos_grupo) > 1 and idx_grupo < len(grupos):
                print(f"\n   ⏸️  Aguardando {delay_entre_grupos}s antes do próximo grupo...\n")
                await asyncio.sleep(delay_entre_grupos)

    def exibir_relatorio(self):
        """Exibe relatório final."""
        print("\n" + "="*70)
        print("📊 RELATÓRIO FINAL")
        print("="*70 + "\n")

        print(f"📦 Grupos de arquivos relacionados: {self.grupos_encontrados}")
        print(f"📁 Total de arquivos: {self.total_encontradas}")
        print(f"✅ Transferidos com sucesso: {self.total_transferidas}")
        print(f"❌ Erros: {self.total_erros}\n")

        if self.total_transferidas > 0:
            taxa = (self.total_transferidas / self.total_encontradas * 100)
            print(f"📈 Taxa de sucesso: {taxa:.1f}%\n")

        print("="*70)
        print("✅ Transferência concluída!" if self.total_transferidas > 0 else "⚠️  Nenhum arquivo transferido.")
        print()


async def main():
    """Função principal."""
    print("\n" + "="*70)
    print("📤 TRANSFERIR ARQUIVOS ORGANIZADOS (Com agrupamento)")
    print("="*70 + "\n")

    print("💡 Este script mantém arquivos Part 1, 2, 3 juntos e organizados!\n")

    # Input
    try:
        fonte_input = input("🔹 ID do grupo FONTE: ").strip()
        fonte_id = int(fonte_input)

        dest_input = input("🔹 ID do grupo DESTINO: ").strip()
        dest_id = int(dest_input)
    except ValueError:
        print("\n❌ ID inválido!\n")
        return

    # Limite
    limit_input = input("\n🔹 Limite de mensagens (Enter para todas): ").strip()
    limit = int(limit_input) if limit_input else None

    # Delays
    delay_input = input("🔹 Delay entre arquivos em segundos (Enter para 0.5): ").strip()
    delay = float(delay_input) if delay_input else 0.5

    delay_grupo_input = input("🔹 Delay entre GRUPOS em segundos (Enter para 2): ").strip()
    delay_grupo = float(delay_grupo_input) if delay_grupo_input else 2.0

    # Transferir
    transferidor = TransferirArquivosOrganizado(API_ID, API_HASH)

    try:
        print("\n" + "="*70)
        print("🔐 CONECTANDO")
        print("="*70 + "\n")

        await transferidor.iniciar()

        if not await transferidor.verificar_grupos(fonte_id, dest_id):
            return

        await transferidor.transferir_todos(
            fonte_id, dest_id,
            limit=limit,
            delay=delay,
            delay_entre_grupos=delay_grupo
        )

        transferidor.exibir_relatorio()

    except KeyboardInterrupt:
        print("\n\n⚠️  Interrompido.")
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
    except Exception as e:
        print(f"\n❌ Erro: {e}\n")
