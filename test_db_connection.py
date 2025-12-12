"""
Script para testar a conexao com o banco de dados Supabase
"""
import os
import sys
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

# Configurar encoding para UTF-8
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

# Carregar variaveis de ambiente do .env
load_dotenv()

def test_connection():
    """Testa a conexao com o banco de dados"""

    # Obter DATABASE_URL do .env
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        print("[X] ERRO: DATABASE_URL nao encontrada no arquivo .env")
        return False

    print("[*] Testando conexao com o banco de dados...")
    print(f"[*] Host: {database_url.split('@')[1].split('/')[0] if '@' in database_url else 'N/A'}")

    try:
        # Criar engine do SQLAlchemy
        engine = create_engine(
            database_url,
            pool_pre_ping=True,
            connect_args={
                "connect_timeout": 10,
                "options": "-c statement_timeout=30000"
            }
        )

        # Tentar conectar e executar um query simples
        with engine.connect() as connection:
            result = connection.execute(text("SELECT version();"))
            version = result.fetchone()[0]

            print("[OK] Conexao bem-sucedida!")
            print(f"[*] Versao do PostgreSQL: {version}")

            # Testar se consegue listar tabelas
            result = connection.execute(text("""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                ORDER BY table_name;
            """))

            tables = result.fetchall()

            if tables:
                print(f"\n[*] Tabelas encontradas ({len(tables)}):")
                for table in tables:
                    print(f"   - {table[0]}")
            else:
                print("\n[!] Nenhuma tabela encontrada no banco (isso e normal se for a primeira execucao)")

            return True

    except Exception as e:
        print(f"[X] ERRO ao conectar ao banco de dados:")
        print(f"   {type(e).__name__}: {str(e)}")

        # Diagnostico adicional
        print("\n[*] Diagnostico:")

        if "password authentication failed" in str(e):
            print("   [X] Senha incorreta ou usuario invalido")
            print("   [*] Verifique se a senha no .env esta correta")
            print("   [!] Dica: Va no Supabase -> Settings -> Database -> Reset Database Password")

        elif "could not connect to server" in str(e) or "timeout" in str(e):
            print("   [X] Nao foi possivel conectar ao servidor")
            print("   [*] Verifique:")
            print("      - Se a URL esta correta")
            print("      - Se voce tem conexao com a internet")
            print("      - Se o projeto Supabase esta ativo")

        elif "SSL" in str(e).upper() or "certificate" in str(e):
            print("   [X] Problema com SSL/certificado")
            print("   [*] Verifique se a URL contem '?sslmode=require'")

        else:
            print("   [!] Erro desconhecido - veja detalhes acima")

        return False

if __name__ == "__main__":
    print("=" * 60)
    print("TESTE DE CONEXAO COM BANCO DE DADOS SUPABASE")
    print("=" * 60)
    print()

    success = test_connection()

    print()
    print("=" * 60)
    if success:
        print("[OK] TESTE CONCLUIDO COM SUCESSO!")
        print("[!] Voce pode iniciar o bot normalmente agora")
    else:
        print("[X] TESTE FALHOU")
        print("[!] Corrija os erros acima antes de iniciar o bot")
    print("=" * 60)
