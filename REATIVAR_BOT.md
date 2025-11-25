# 🔄 GUIA: Reativar Bot que Parou de Enviar

## 🚨 Problema
O bot parou de enviar mensagens / não está respondendo.

---

## ⚡ SOLUÇÃO RÁPIDA (Tente primeiro)

### 1. Reiniciar o Bot

```powershell
# Se o bot estiver rodando, pare com Ctrl+C

# Depois inicie novamente
python main.py
```

Teste no Telegram: `/start`

---

## 🔍 DIAGNÓSTICO COMPLETO

Se o reinício simples não resolver, execute o diagnóstico:

```powershell
python diagnostico_bot.py
```

Este script vai verificar:
- ✅ Conexão com bot do Telegram
- ✅ Sessão do Pyrogram (User API)
- ✅ Banco de dados
- ✅ Envio de mensagens
- ✅ Processos rodando

---

## 🛠️ REATIVAR CONEXÕES

Se o diagnóstico mostrar problemas, execute:

```powershell
python reativar_bot.py
```

Este script vai:
1. Testar bot do Telegram
2. Reativar sessão do Pyrogram (se necessário)
3. Enviar mensagem de teste
4. Mostrar próximos passos

---

## 🐛 PROBLEMAS COMUNS E SOLUÇÕES

### 1. Sessão do Pyrogram Expirada

**Sintomas**:
- Bot não envia mensagens automáticas
- Erro: "Session expired"
- auto_sender.py não funciona

**Solução**:
```powershell
python reativar_bot.py
```
Escolha 's' quando perguntar sobre reativar Pyrogram e faça login novamente.

---

### 2. Bot não está rodando

**Sintomas**:
- Nenhum processo Python rodando
- Bot não responde a comandos

**Solução**:
```powershell
# Certifique-se que não há outro processo rodando
python main.py
```

---

### 3. Token do Bot Inválido

**Sintomas**:
- Erro: "Unauthorized" ou "Invalid token"
- Bot não conecta

**Solução**:
1. Verifique `.env`: `BOT_TOKEN=...`
2. Confirme o token com @BotFather no Telegram
3. Se necessário, crie novo token e atualize `.env`

---

### 4. Erro de Permissões

**Sintomas**:
- Erro ao enviar mensagens
- "Forbidden" ou "Bot was blocked"

**Solução**:
1. Certifique-se que você iniciou conversa com o bot
2. Verifique se não bloqueou o bot
3. No Telegram, envie `/start` para o bot

---

### 5. Pyrogram não conecta

**Sintomas**:
- Erro ao fazer login
- "Phone number invalid"
- "Session file not found"

**Solução Completa**:

```powershell
# 1. Pare o bot (Ctrl+C)

# 2. Delete sessões antigas
del my_account.session
del my_account.session-journal

# 3. Reative
python reativar_bot.py

# 4. Faça login quando solicitado:
#    - Digite seu número: +55xxxxx
#    - Digite o código recebido
#    - Se pedido, digite senha 2FA
```

---

## 🔧 COMANDOS ÚTEIS

### Verificar se bot está rodando:
```powershell
# Windows (PowerShell)
Get-Process python
```

### Parar todos os processos Python (se travar):
```powershell
# Windows (PowerShell)
Stop-Process -Name "python" -Force
```

### Ver logs do bot:
```powershell
# Se você configurou logs
type logs\bot.log
```

---

## 📋 CHECKLIST DE VERIFICAÇÃO

Antes de iniciar o bot, confirme:

- [ ] Arquivo `.env` existe e está configurado
- [ ] `BOT_TOKEN` está correto
- [ ] Conexão com internet está OK
- [ ] Não há outro processo do bot rodando
- [ ] Sessão do Pyrogram está ativa (se usar auto_sender)
- [ ] Você enviou `/start` para o bot no Telegram

---

## 🚀 INICIAR O BOT CORRETAMENTE

### Opção 1: Bot Normal (Apenas comandos)

```powershell
python main.py
```

Este inicia o bot básico com comandos como `/pagar`, `/tx`, etc.

### Opção 2: Auto Sender (Envio automático + comandos)

```powershell
python auto_sender.py
```

Este inicia o bot completo com envio automático de arquivos.

**Escolha Opção 2 se**:
- Você quer enviar arquivos automaticamente
- Precisa do Pyrogram ativo
- Quer funcionalidades completas

---

## 🆘 AINDA NÃO FUNCIONA?

Execute diagnóstico detalhado:

```powershell
python diagnostico_bot.py
```

E me envie a saída completa para análise.

---

## 💡 DICAS PARA EVITAR PROBLEMAS

1. **Não rode múltiplas instâncias** do bot ao mesmo tempo
2. **Sempre pare corretamente** com Ctrl+C antes de fechar
3. **Mantenha sessão do Pyrogram ativa** fazendo login periodicamente
4. **Verifique .env** após fazer alterações
5. **Teste após cada mudança** importante

---

## ✅ TESTE FINAL

Depois de reativar, teste:

1. No Telegram, envie para seu bot:
```
/start
/pagar
/tx
```

2. O bot deve responder a todos os comandos

3. Se usar auto_sender, verifique se envia arquivos automaticamente

---

**Se tudo funcionar**: 🎉 Bot reativado com sucesso!

**Se ainda houver problemas**: Execute `python diagnostico_bot.py` e analise os erros.
