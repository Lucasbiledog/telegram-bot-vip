# 📱 COMANDOS DO BOT NO TELEGRAM

**IMPORTANTE**: Todos estes comandos devem ser enviados **NO TELEGRAM**, conversando com o bot **@UnrealPack5_bot**

---

## 📊 COMANDOS DE ESTATÍSTICAS

### `/stats`
Ver estatísticas completas do sistema

**Como usar**:
1. Abra o Telegram
2. Procure por **@UnrealPack5_bot**
3. Digite: `/stats`

**Resposta**:
```
📊 Estatísticas do Sistema

📦 Arquivos indexados:
   • VIP: 1847 arquivos disponíveis
   • FREE: 1847 arquivos disponíveis

📤 Arquivos enviados:
   • VIP: 15 enviados
   • FREE: 2 enviados

💾 Banco de dados: Conectado
```

---

### `/history vip` ou `/history free`
Ver histórico de envios

**Exemplo**:
```
/history vip
```

**Resposta**:
```
📜 Histórico de Envios VIP (últimos 10)

1. arquivo_001.mp4 - 14/12/2025 15:00
2. arquivo_002.zip - 13/12/2025 15:00
3. arquivo_003.rar - 12/12/2025 15:00
...
```

---

## 🎯 COMANDOS DE TESTE

### `/test_send vip` ou `/test_send free`
Testar envio manual (sem esperar até 15h)

**Como usar**:
```
/test_send vip
```

**O que faz**:
- Pega 1 arquivo aleatório do banco
- Envia para o canal (VIP ou FREE)
- Marca como enviado

**Resposta**:
```
✅ Arquivo enviado com sucesso!

📦 Arquivo: video_pack_001.mp4
📏 Tamanho: 345 MB
📍 Canal: VIP (-1003255098941)
🕐 Enviado às: 15:23
```

---

### `/next_file vip` ou `/next_file free`
Ver qual será o próximo arquivo a ser enviado

**Exemplo**:
```
/next_file vip
```

**Resposta**:
```
📦 Próximo arquivo VIP:

📄 Nome: arquivo_premium_2025.zip
📏 Tamanho: 1.2 GB
📝 Caption: Conteúdo exclusivo VIP
🕐 Será enviado às: 15/12/2025 15:00
```

---

## 🕐 COMANDOS DE AGENDAMENTO

### `/list_jobs`
Ver todos os jobs agendados

**Resposta**:
```
🕐 Jobs agendados:

📧 VIP Diário:
   • Horário: 15:00 (todos os dias)
   • Próximo envio: 15/12/2025 15:00
   • Status: ✅ Ativo

📧 FREE Semanal:
   • Horário: 15:00 (quartas-feiras)
   • Próximo envio: 17/12/2025 15:00
   • Status: ✅ Ativo

🎁 FREE Promo:
   • Horário: 15:30 (quartas-feiras)
   • Próximo envio: 17/12/2025 15:30
   • Status: ✅ Ativo
```

---

### `/schedule vip HORARIO` ou `/schedule free HORARIO`
Alterar horário de envio (SOMENTE ADMIN)

**Exemplo**:
```
/schedule vip 16:00
```

**Resposta**:
```
✅ Horário VIP alterado!

⏰ Novo horário: 16:00
🌍 Fuso: America/Sao_Paulo
📅 Próximo envio: 15/12/2025 16:00
```

---

## 🔍 COMANDOS DE SCAN

### `/scan_full`
Fazer scan completo do grupo fonte

**Como usar**:
```
/scan_full
```

**O que acontece**:
1. Bot vai te enviar um código SMS
2. Digite o código (no Telegram, como resposta)
3. Bot vai escanear TODO o histórico do grupo fonte
4. Arquivos serão indexados no banco

**Resposta**:
```
🔍 Iniciando scan completo...

⏳ Aguarde... pode demorar vários minutos

📊 Progresso:
   • 500 mensagens processadas
   • 387 arquivos indexados
   • 113 duplicados (já existiam)

✅ Scan finalizado!
💾 Total no banco: 1847 arquivos
```

---

## 👥 COMANDOS DE ADMINISTRAÇÃO

### `/addadmin @usuario`
Adicionar novo admin (SOMENTE OWNER)

**Exemplo**:
```
/addadmin @fulano
```

---

### `/removeadmin @usuario`
Remover admin (SOMENTE OWNER)

**Exemplo**:
```
/removeadmin @fulano
```

---

### `/listadmins`
Listar todos os admins

**Resposta**:
```
👥 Lista de Admins:

1. @owner (ID: 8520246396) - OWNER
2. @admin1 (ID: 123456789) - Admin
3. @admin2 (ID: 987654321) - Admin
```

---

## 💎 COMANDOS VIP

### `/addvip @usuario DIAS`
Adicionar usuário VIP (ADMIN)

**Exemplo**:
```
/addvip @fulano 30
```

**Resposta**:
```
✅ Usuário @fulano adicionado ao VIP!

⏰ Duração: 30 dias
📅 Expira em: 14/01/2026
🎁 Acesso garantido ao grupo VIP
```

---

### `/removevip @usuario`
Remover usuário do VIP (ADMIN)

**Exemplo**:
```
/removevip @fulano
```

---

### `/listvip`
Listar todos os usuários VIP (ADMIN)

**Resposta**:
```
💎 Usuários VIP Ativos:

1. @user1 - Expira em: 20/12/2025 (5 dias)
2. @user2 - Expira em: 15/01/2026 (31 dias)
3. @user3 - Expira em: 01/02/2026 (48 dias)

Total: 3 usuários VIP
```

---

### `/checkvip @usuario`
Verificar status VIP de um usuário

**Exemplo**:
```
/checkvip @fulano
```

**Resposta**:
```
💎 Status VIP de @fulano:

✅ VIP Ativo
📅 Desde: 15/11/2025
⏰ Expira em: 15/01/2026
⏳ Faltam: 31 dias
```

---

## 🛠️ COMANDOS DE SISTEMA

### `/health`
Verificar saúde do sistema

**Resposta**:
```
🏥 Status do Sistema

✅ Bot: Online
✅ Banco de dados: Conectado
✅ Jobs: 3 ativos
✅ Canais: Acessíveis
✅ API CoinGecko: OK (10 req/min)

⏱️ Uptime: 23h 45m
💾 Memória: 245 MB / 512 MB
```

---

### `/reload`
Recarregar configurações (ADMIN)

**Resposta**:
```
🔄 Configurações recarregadas!

✅ .env recarregado
✅ Jobs reagendados
✅ Cache limpo
```

---

## ℹ️ COMANDOS DE AJUDA

### `/help`
Ver lista de comandos

**Resposta**:
```
📚 Comandos Disponíveis

📊 Estatísticas:
   • /stats - Ver estatísticas
   • /history - Histórico de envios

🎯 Testes:
   • /test_send - Enviar teste
   • /next_file - Ver próximo arquivo

🕐 Agendamento:
   • /list_jobs - Ver jobs
   • /schedule - Alterar horário

Para ver comandos de admin, use /help admin
```

---

### `/start`
Iniciar conversa com o bot

**Resposta**:
```
👋 Bem-vindo ao UnrealPack Bot!

🎯 Sistema de distribuição automática de arquivos VIP

📊 Use /stats para ver estatísticas
📚 Use /help para ver comandos
💎 Use /subscribe para se tornar VIP

Bot desenvolvido com 🤖 Claude Code
```

---

## 📝 OBSERVAÇÕES IMPORTANTES

### ⚠️ **NÃO FUNCIONA NO TERMINAL**

Estes comandos SÃO ENVIADOS NO TELEGRAM, NÃO no terminal/CMD!

❌ **ERRADO**:
```bash
# No terminal (CMD)
/stats
```

✅ **CERTO**:
```
No Telegram → @UnrealPack5_bot → /stats
```

---

### 🔐 **Comandos Restritos**

Alguns comandos são restritos:

- **OWNER** (ID: 8520246396):
  - `/addadmin`, `/removeadmin`
  - `/reload`

- **ADMIN**:
  - `/addvip`, `/removevip`, `/listvip`
  - `/schedule`

- **TODOS**:
  - `/stats`, `/help`, `/start`
  - `/history`, `/next_file`

---

## 🚀 COMANDOS MAIS USADOS

Para uso diário:

1. **Ver estatísticas**:
   ```
   /stats
   ```

2. **Testar envio**:
   ```
   /test_send vip
   ```

3. **Ver jobs**:
   ```
   /list_jobs
   ```

4. **Adicionar VIP** (admin):
   ```
   /addvip @usuario 30
   ```

---

## 🆘 PRECISA DE AJUDA?

Se tiver dúvidas:

1. Use `/help` no bot
2. Veja o arquivo `COMO_USAR.md`
3. Verifique os logs do Render
