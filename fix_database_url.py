"""
Script para corrigir a DATABASE_URL codificando caracteres especiais
"""
from urllib.parse import quote_plus

# Componentes da conexao
username = "postgres.pghjvkgawvkyjhrlpjes"
password = "Lcsdm5941##%"  # Senha original com caracteres especiais
host = "aws-1-sa-east-1.pooler.supabase.com"
port = "6543"
database = "postgres"
params = "sslmode=require&connect_timeout=10"

# Codificar a senha para URL (## vira %23%23 e % vira %25)
password_encoded = quote_plus(password)

# Construir a URL completa
database_url = f"postgresql://{username}:{password_encoded}@{host}:{port}/{database}?{params}"

print("=" * 80)
print("CORRECAO DA DATABASE_URL")
print("=" * 80)
print()
print("Senha original:", password)
print("Senha codificada:", password_encoded)
print()
print("DATABASE_URL corrigida:")
print(database_url)
print()
print("=" * 80)
print("COPIE A URL ACIMA E SUBSTITUA NO ARQUIVO .env NA LINHA 85")
print("=" * 80)
