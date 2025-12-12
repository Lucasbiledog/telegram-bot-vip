"""
Testa diferentes variacoes da connection string
"""
import sys
from sqlalchemy import create_engine, text

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

password = "qtNPlIw04gUZSI3u"
host = "aws-1-sa-east-1.pooler.supabase.com"
port = "6543"
database = "postgres"

# Tentar diferentes usuarios
users_to_test = [
    "postgres.pghjvkgawvkyjhrlpjes",
    "postgres",
    "pghjvkgawvkyjhrlpjes"
]

print("=" * 60)
print("TESTANDO DIFERENTES VARIANTES DE USUARIO")
print("=" * 60)

for user in users_to_test:
    print(f"\n[*] Tentando com usuario: {user}")
    database_url = f"postgresql://{user}:{password}@{host}:{port}/{database}?sslmode=require&connect_timeout=10"

    try:
        engine = create_engine(database_url, pool_pre_ping=True)
        with engine.connect() as connection:
            result = connection.execute(text("SELECT 1"))
            result.fetchone()
            print(f"[OK] SUCESSO! Usuario correto: {user}")
            print(f"\nDATABASE_URL correta:")
            print(database_url)
            break
    except Exception as e:
        if "password authentication failed" in str(e):
            print(f"[X] Falhou - usuario ou senha invalidos")
        else:
            print(f"[X] Erro: {str(e)[:100]}")

print("\n" + "=" * 60)
