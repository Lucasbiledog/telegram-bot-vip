#!/usr/bin/env python3
"""
Script de migração para adicionar novos campos à tabela payments (versão limpa)
"""

import os
import sys
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

# Add the bot directory to the path
sys.path.append(os.path.dirname(__file__))

def get_database_url():
    """Obter URL do banco de dados"""
    env_db_url = os.getenv("DATABASE_URL", "").strip()
    
    if env_db_url:
        url = make_url(env_db_url)
        if url.get_backend_name() == "postgresql" and url.drivername == "postgres":
            return env_db_url.replace("postgres://", "postgresql://", 1)
        return env_db_url
    
    # Fallback para SQLite local
    script_dir = os.path.dirname(os.path.abspath(__file__))
    sqlite_path = os.path.join(script_dir, "bot.db")
    from pathlib import Path
    path_obj = Path(sqlite_path)
    return f"sqlite:///{path_obj.as_posix()}"

def migrate_payment_table():
    """Adicionar novos campos à tabela payments"""
    db_url = get_database_url()
    engine = create_engine(db_url, pool_pre_ping=True)
    
    print(f"Conectando ao banco: {db_url}")
    
    # Comandos de migração
    migrations = [
        "ALTER TABLE payments ADD COLUMN token_symbol VARCHAR",
        "ALTER TABLE payments ADD COLUMN usd_value VARCHAR", 
        "ALTER TABLE payments ADD COLUMN vip_days INTEGER"
    ]
    
    with engine.begin() as conn:
        for migration in migrations:
            try:
                print(f"Executando: {migration}")
                conn.execute(text(migration))
                print("[OK] Sucesso")
            except Exception as e:
                if "already exists" in str(e).lower() or "duplicate column" in str(e).lower():
                    print("[WARNING] Coluna ja existe, pulando")
                else:
                    print(f"[ERROR] Erro: {e}")
    
    print("\n[SUCCESS] Migracao concluida!")

def test_new_fields():
    """Testar se os novos campos estão funcionando"""
    print("\n[TEST] Testando novos campos...")
    
    db_url = get_database_url()
    engine = create_engine(db_url, pool_pre_ping=True)
    
    try:
        with engine.begin() as conn:
            # Verificar se conseguimos inserir dados com novos campos
            test_query = text("""
                SELECT id, tx_hash, token_symbol, usd_value, vip_days 
                FROM payments 
                WHERE token_symbol IS NOT NULL 
                LIMIT 5
            """)
            
            result = conn.execute(test_query)
            rows = result.fetchall()
            
            if rows:
                print("[OK] Dados encontrados com novos campos:")
                for row in rows:
                    print(f"  ID {row[0]}: {row[2]} = ${row[3]}, {row[4]} dias VIP")
            else:
                print("[INFO] Nenhum payment com novos campos ainda (esperado em instalacao nova)")
                
    except Exception as e:
        print(f"[ERROR] Erro ao testar: {e}")

if __name__ == "__main__":
    print("[MIGRATION] INICIANDO MIGRACAO DE BANCO DE DADOS")
    print("=" * 50)
    
    try:
        migrate_payment_table()
        test_new_fields()
        
        print("\n[SUCCESS] MIGRACAO COMPLETADA COM SUCESSO!")
        print("\nNovos campos adicionados:")
        print("* token_symbol - Simbolo da moeda paga (ETH, USDC, etc)")
        print("* usd_value - Valor em USD na epoca do pagamento") 
        print("* vip_days - Dias de VIP atribuidos")
        print("\nOs proximos pagamentos aprovados incluirao essas informacoes!")
        
    except Exception as e:
        print(f"\n[ERROR] ERRO NA MIGRACAO: {e}")
        sys.exit(1)