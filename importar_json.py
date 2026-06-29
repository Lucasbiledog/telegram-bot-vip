#!/usr/bin/env python3
"""
Importa arquivos indexados do JSON para o banco de dados.
Execute este script NO SERVIDOR (Render) ou localmente após gerar o JSON.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Adicionar ao path
sys.path.insert(0, str(Path(__file__).parent))

from main import SessionLocal, SourceFile

def importar_arquivos(json_file='arquivos_indexados.json'):
    """Importa arquivos do JSON para o banco de dados"""

    print("\n" + "="*70)
    print("📥 IMPORTANDO ARQUIVOS PARA O BANCO")
    print("="*70)

    # Ler JSON
    try:
        with open(json_file, 'r', encoding='utf-8') as f:
            arquivos = json.load(f)
    except FileNotFoundError:
        print(f"\n❌ Arquivo {json_file} não encontrado!")
        print("\n💡 Execute primeiro: python indexar_historico_local.py\n")
        return
    except json.JSONDecodeError:
        print(f"\n❌ Erro ao ler JSON. Arquivo corrompido?\n")
        return

    print(f"\n📁 {len(arquivos)} arquivos no JSON")
    print("⏳ Importando para o banco...\n")

    stats = {
        'importados': 0,
        'duplicados': 0,
        'erros': 0
    }

    with SessionLocal() as session:
        for i, arq in enumerate(arquivos, 1):
            # Progress a cada 100
            if i % 100 == 0:
                print(f"   📊 {i}/{len(arquivos)} processados | "
                      f"✅ {stats['importados']} importados | "
                      f"⏭️ {stats['duplicados']} duplicados")

            try:
                # Verificar se já existe
                existing = session.query(SourceFile).filter(
                    SourceFile.file_unique_id == arq['file_unique_id']
                ).first()

                if existing:
                    stats['duplicados'] += 1
                    continue

                # Criar novo
                source_file = SourceFile(
                    file_id=arq['file_id'],
                    file_unique_id=arq['file_unique_id'],
                    file_type=arq['file_type'],
                    message_id=arq['message_id'],
                    source_chat_id=arq['source_chat_id'],
                    caption=arq.get('caption'),
                    file_name=arq.get('file_name'),
                    file_size=arq.get('file_size'),
                    indexed_at=datetime.now(timezone.utc),
                    active=True
                )
                session.add(source_file)
                session.commit()

                stats['importados'] += 1

            except Exception as e:
                session.rollback()
                stats['erros'] += 1
                print(f"   ❌ Erro no arquivo {i}: {e}")

        # Relatório final
        print("\n" + "="*70)
        print("✅ IMPORTAÇÃO CONCLUÍDA!")
        print("="*70)
        print(f"\n📊 ESTATÍSTICAS:")
        print(f"   ✅ Importados: {stats['importados']}")
        print(f"   ⏭️  Duplicados: {stats['duplicados']}")
        print(f"   ❌ Erros: {stats['erros']}")

        # Total no banco
        total_banco = session.query(SourceFile).filter(
            SourceFile.active == True
        ).count()

        print(f"\n💾 Total no banco agora: {total_banco} arquivos")
        print("\n✅ Pronto! Use /stats_auto no bot para ver as estatísticas.\n")


if __name__ == "__main__":
    try:
        importar_arquivos()
    except KeyboardInterrupt:
        print("\n\n❌ Cancelado pelo usuário.\n")
    except Exception as e:
        print(f"\n❌ Erro: {e}\n")
        import traceback
        traceback.print_exc()
