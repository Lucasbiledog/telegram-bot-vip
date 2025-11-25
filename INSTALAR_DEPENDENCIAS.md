# 📦 Como Instalar Dependências do Bot

## ⚡ SOLUÇÃO RÁPIDA

### Método 1: Arquivo BAT (Mais Fácil)

Dê duplo clique no arquivo:
```
instalar_tudo.bat
```

Ele vai procurar Python e instalar tudo automaticamente!

---

### Método 2: PowerShell

Abra o PowerShell na pasta do bot e tente cada comando até funcionar:

#### Opção A - Comando `python`:
```powershell
python -m pip install -r requirements.txt
```

#### Opção B - Comando `py`:
```powershell
py -m pip install -r requirements.txt
```

#### Opção C - Caminho completo:
```powershell
# Substitua pelo caminho onde seu Python está instalado
C:\Python312\python.exe -m pip install -r requirements.txt
```

---

## 🔍 Encontrar onde Python está instalado

No PowerShell:

```powershell
# Tentar encontrar Python
where.exe python
where.exe py

# Ou verificar versão
python --version
py --version
```

---

## 📋 O que será instalado

O arquivo `requirements.txt` vai instalar:

- ✅ `python-telegram-bot` - Bot do Telegram
- ✅ `web3` - Conexão com blockchains
- ✅ `httpx` - Requisições HTTP
- ✅ `SQLAlchemy` - Banco de dados
- ✅ `pyrogram` - API do Telegram (user)
- ✅ `fastapi` - Servidor web (checkout)
- ✅ E mais...

**Tamanho aproximado**: ~200-300 MB
**Tempo estimado**: 2-5 minutos

---

## ❌ Se Python não estiver instalado

### Baixar e Instalar Python:

1. Acesse: https://www.python.org/downloads/
2. Baixe Python 3.11 ou 3.12
3. **IMPORTANTE**: Durante instalação, marque:
   - ✅ "Add Python to PATH"
   - ✅ "Install pip"
4. Reinicie o PowerShell
5. Teste: `python --version`

---

## 🐛 Problemas Comuns

### "Python não é reconhecido..."

**Solução**: Python não está no PATH

1. Procure onde Python foi instalado:
   - Padrão: `C:\Users\SEU_USUARIO\AppData\Local\Programs\Python\`
   - Ou: `C:\Python312\`

2. Use caminho completo:
```powershell
C:\caminho\do\python.exe -m pip install -r requirements.txt
```

---

### "pip não é reconhecido..."

**Solução**: Instalar pip manualmente

```powershell
python -m ensurepip --upgrade
python -m pip install --upgrade pip
```

---

### "Permission denied" ou "Access denied"

**Solução**: Executar como Administrador

1. Feche PowerShell
2. Clique direito no PowerShell
3. "Executar como Administrador"
4. Tente novamente

Ou instale só para seu usuário:
```powershell
python -m pip install --user -r requirements.txt
```

---

### Erro ao instalar algum pacote específico

**Solução**: Atualizar pip e setuptools

```powershell
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt
```

---

## ✅ Verificar Instalação

Depois de instalar, teste:

```powershell
python -c "import telegram; print('telegram OK')"
python -c "import web3; print('web3 OK')"
python -c "import httpx; print('httpx OK')"
```

Se todos mostrarem "OK", está pronto! ✅

---

## 🚀 Próximos Passos

Depois de instalar as dependências:

```powershell
# 1. Verificar configuração
python check_config.py

# 2. Diagnosticar bot
python diagnostico_bot.py

# 3. Iniciar bot
python main.py
```

---

## 💡 Dica

Se tiver muitos problemas com Python no Windows, considere usar:

### Python via Microsoft Store:
1. Abra Microsoft Store
2. Procure "Python 3.12"
3. Instale
4. Use comando: `python` ou `python3`

Geralmente funciona sem problemas de PATH!
