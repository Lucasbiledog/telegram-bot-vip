# 🚀 COMO USAR O SISTEMA COMPLETO

Este guia mostra como configurar e usar o bot do ZERO até os envios automáticos funcionando.

---

## 📋 ÍNDICE

1. [Configurar Banco de Dados (Supabase)](#1-configurar-banco-de-dados)
2. [Indexar Arquivos do Grupo Fonte](#2-indexar-arquivos)
3. [Configurar Bot no Render](#3-configurar-bot-no-render)
4. [Verificar Sistema Funcionando](#4-verificar-sistema)

---

## 1️⃣ CONFIGURAR BANCO DE DADOS

### **Por que Supabase?**
- ✅ **500 MB grátis PERMANENTE** (Render expira em 30 dias)
- ✅ **Região São Paulo** (baixa latência)
- ✅ **Backups automáticos**

### **Passo a passo**:

Siga o guia completo: **[SUPABASE_SETUP.md](./SUPABASE_SETUP.md)**

**Resumo**:
1. Crie conta em https://supabase.com/
2. Crie projeto (região: South America - São Paulo)
3. Copie a **Connection String** da aba "Connection pooling" (porta 6543)
4. Adicione: `?sslmode=require&connect_timeout=10`
5. Configure no Render (Environment → DATABASE_URL)

**URL CORRETA**:
```
postgresql://postgres.xxx:SuaSenha@aws-0-sa-east-1.pooler.supabase.com:6543/postgres?sslmode=require&connect_timeout=10
```

---

## 2️⃣ INDEXAR ARQUIVOS DO GRUPO FONTE

### **O que faz**:
- Usa sua conta do Telegram (Pyrogram) para ler TODO o histórico
- Salva file_id, message_id, caption, etc. no banco
- Bot consulta o banco e envia 1 arquivo/dia (VIP) + 1/semana (FREE)

### **Opção A: Rodar Localmente** (RECOMENDADO)

**ONDE EXECUTAR**: No **terminal/CMD do seu computador** (NÃO no Telegram!)

---

1. **Abra o arquivo** `scan_local.py` num editor de texto

2. **Configure as credenciais** (linhas 27-49):

```python
# 1. Pyrogram - Obtenha em: https://my.telegram.org/apps
TELEGRAM_API_ID = "21891661"  # Já está configurado!
TELEGRAM_API_HASH = "3011acf0afc4bff11cfa8fc5c42207f9"  # Já está!

# 2. Database (Supabase) - COLE SUA URL AQUI
DATABASE_URL = "postgresql://postgres.xxx:senha@aws-0-sa-east-1.pooler.supabase.com:6543/postgres?sslmode=require&connect_timeout=10"

# 3. ID do grupo fonte
SOURCE_CHAT_ID = -1003080645605  # Já está configurado!
```

3. **Rode o script NO TERMINAL/CMD**:

**Windows** (CMD ou PowerShell):
```bash
cd "C:\Users\Infratech Engenharia\OneDrive - Infratech Engenharia LTDA\Documentos\telegram-bot-vip-master\telegram-bot-vip-master"
python scan_local.py
```

**Linux/Mac**:
```bash
cd /caminho/para/telegram-bot-vip-master
python3 scan_local.py
```

4. **Na primeira vez**:
   - O Telegram vai enviar um código SMS para seu celular
   - Digite o código no terminal (NÃO no Telegram!)
   - Aguarde o scan completar (pode demorar vários minutos)

5. **Saída esperada no terminal**:
```
🔌 Conectando ao banco de dados...
✅ Conectado ao banco com sucesso!

🔄 Iniciando autenticação...
👤 Autenticado como: Seu Nome
✅ Grupo encontrado: Banco de Arquivos VIP

🔍 Escaneando mensagens...
⏳ Isso pode demorar vários minutos...

📊 Progresso: 100 mensagens | Indexadas: 87 | Duplicadas: 0
📊 Progresso: 200 mensagens | Indexadas: 174 | Duplicadas: 0
...

═══════════════════════════════════════════════════════════════
📊 RELATÓRIO FINAL
═══════════════════════════════════════════════════════════════

📨 Mensagens processadas: 5230
✅ Novas indexadas: 1847
⏭️  Já existentes: 0
❌ Erros: 0

📁 Tipos de arquivo encontrados:
   • document: 1520
   • video: 327

💾 Total no banco: 1847 arquivos

═══════════════════════════════════════════════════════════════
✅ SCAN COMPLETO FINALIZADO!
═══════════════════════════════════════════════════════════════
```

---

### **Opção B: Usar Comando no Bot**

**ONDE EXECUTAR**: No **Telegram**, conversando com **@UnrealPack5_bot**

---

1. **Abra o Telegram** → Procure por **@UnrealPack5_bot**
2. **Digite**:
   ```
   /scan_full
   ```
3. **Digite o código SMS** que você receberá no celular
4. **Aguarde** o scan completar

**Nota**: Essa opção pode ser mais lenta e depende do bot estar rodando no Render.

---

## 3️⃣ CONFIGURAR BOT NO RENDER

### **3.1 - Verificar Variáveis de Ambiente**

No painel do Render (Environment), certifique-se de ter:

```env
# Bot Telegram
BOT_TOKEN=8535216703:AAHGr1uEnO2HaF3At0s4-EGoB7_5zLMzbbE

# Pyrogram (para scan)
TELEGRAM_API_ID=21891661
TELEGRAM_API_HASH=3011acf0afc4bff11cfa8fc5c42207f9

# Banco Supabase
DATABASE_URL=postgresql://postgres.xxx:senha@aws-0-sa-east-1.pooler.supabase.com:6543/postgres?sslmode=require&connect_timeout=10

# Canais
VIP_CHANNEL_ID=-1003255098941
FREE_CHANNEL_ID=-1002777289859
SOURCE_CHAT_ID=-1003080645605
```

### **3.2 - Deploy**

Se tudo estiver configurado:
1. Vá em "Manual Deploy" → "Deploy latest commit"
2. Aguarde ~3 minutos
3. Verifique os logs

---

## 4️⃣ VERIFICAR SISTEMA FUNCIONANDO

### **4.1 - Verificar Logs**

Nos logs do Render, procure por:

```
✅ [DB] Conexão estabelecida com sucesso!
✅ [SCHEMA] Schema inicializado
✅ Bot inicializado com sucesso!
✅ Job VIP diário configurado (15h)
✅ Job FREE arquivo configurado (15h quartas)
```

---

### **4.2 - Comandos de Teste**

**IMPORTANTE**: Estes comandos são enviados **NO TELEGRAM**, conversando com o bot @UnrealPack5_bot

---

#### **📊 Ver estatísticas**:

**Abra o Telegram** → Abra conversa com **@UnrealPack5_bot** → Digite:
```
/stats
```

**Resposta esperada**:
```
📊 Estatísticas do Sistema

📦 Arquivos indexados:
   • VIP: 1847 arquivos disponíveis
   • FREE: 1847 arquivos disponíveis

📤 Arquivos enviados:
   • VIP: 0 enviados
   • FREE: 0 enviados

💾 Banco de dados: Conectado
```

---

#### **🎯 Testar envio VIP**:

**No Telegram** com **@UnrealPack5_bot**:
```
/test_send vip
```

**O que acontece**:
- Bot pega 1 arquivo aleatório do banco
- Envia para o canal VIP (-1003255098941)
- Marca como enviado no banco

---

#### **🎯 Testar envio FREE**:

**No Telegram** com **@UnrealPack5_bot**:
```
/test_send free
```

**O que acontece**:
- Bot pega 1 arquivo aleatório (max 500MB)
- Envia para o canal FREE (-1002777289859)
- Marca como enviado no banco

---

#### **🕐 Ver jobs agendados**:

**No Telegram** com **@UnrealPack5_bot**:
```
/list_jobs
```

**Resposta esperada**:
```
🕐 Jobs agendados:

📧 VIP Diário:
   • Horário: 15:00 (todos os dias)
   • Próximo envio: 15/12/2025 15:00

📧 FREE Semanal:
   • Horário: 15:00 (quartas-feiras)
   • Próximo envio: 17/12/2025 15:00

🎁 FREE Promo:
   • Horário: 15:30 (quartas-feiras)
   • Próximo envio: 17/12/2025 15:30
```

---

## 🎯 SISTEMA AUTOMÁTICO

### **Horários de Envio**:

| Tier | Quando | Horário | Fuso |
|------|--------|---------|------|
| **VIP** | Todos os dias | 15:00 | America/Sao_Paulo |
| **FREE** | Quartas-feiras | 15:00 | America/Sao_Paulo |
| **Promo FREE** | Quartas-feiras | 15:30 | America/Sao_Paulo |

### **Como funciona**:

1. **Todos os dias às 15h**:
   - Bot busca arquivo aleatório da tabela `source_files`
   - Filtra arquivos que ainda NÃO foram enviados para VIP
   - Envia para o canal VIP (-1003255098941)
   - Marca como enviado no banco

2. **Quartas-feiras às 15h**:
   - Bot busca arquivo aleatório (max 500MB, sem partes)
   - Filtra arquivos que ainda NÃO foram enviados para FREE
   - Envia para o canal FREE (-1002777289859)
   - Marca como enviado

3. **Quartas-feiras às 15:30**:
   - Envia mensagem promocional para o canal FREE

---

## 🔄 ADICIONAR MAIS ARQUIVOS

Quando você adicionar novos arquivos no grupo fonte:

### **Opção 1: Scan Completo** (recomendado 1x/semana)

**No terminal/CMD do computador**:
```bash
python scan_local.py
```

Vai indexar APENAS arquivos novos (pula duplicados).

**Exemplo de saída**:
```
📨 Mensagens processadas: 5430
✅ Novas indexadas: 200  ← Só os novos!
⏭️  Já existentes: 5230  ← Já estavam no banco
❌ Erros: 0
```

---

### **Opção 2: Indexação Automática**

O bot já tem indexação automática configurada!

Quando um arquivo novo é postado no grupo fonte (-1003080645605), o bot detecta e indexa automaticamente.

**Como verificar** (nos logs do Render):
```
[AUTO-INDEX] Novo arquivo detectado: video.mp4
[AUTO-INDEX] ✅ Indexado: ID 12345
```

---

## 📊 MONITORAMENTO

### **Ver quantos arquivos há disponíveis**:

**No Telegram** → **@UnrealPack5_bot**:
```
/stats
```

### **Ver próximo arquivo que será enviado**:

**No Telegram** → **@UnrealPack5_bot**:
```
/next_file vip
```
ou
```
/next_file free
```

### **Ver histórico de envios**:

**No Telegram** → **@UnrealPack5_bot**:
```
/history vip
```
ou
```
/history free
```

---

## 🛠️ SOLUÇÃO DE PROBLEMAS

### ❌ **Bot não está enviando às 15h**

**Causas**:
1. Banco de dados não conectado
2. Nenhum arquivo indexado
3. Todos os arquivos já foram enviados

**Soluções**:
1. Verifique logs: procure por `✅ [DB] Conexão estabelecida`
2. Rode `/stats` - deve ter arquivos indexados
3. Rode `/test_send vip` para testar manualmente

---

### ❌ **Scan não funciona**

**Causas**:
1. DATABASE_URL incorreto (porta 5432 ao invés de 6543)
2. TELEGRAM_API_ID/HASH não configurados
3. Conta não está no grupo fonte

**Soluções**:
1. Verifique se a URL tem `.pooler.supabase.com` e porta `6543`
2. Obtenha API ID em: https://my.telegram.org/apps
3. Entre no grupo fonte com sua conta

---

### ❌ **"Network is unreachable"**

**Causa**: URL do banco com porta errada

**Solução**: Veja [CORRIGIR_ERRO_BANCO.md](./CORRIGIR_ERRO_BANCO.md)

---

## ✅ CHECKLIST FINAL

Antes de considerar tudo configurado:

- [ ] Supabase criado e connection string copiada
- [ ] DATABASE_URL configurado no Render (porta 6543!)
- [ ] Scan executado com sucesso (arquivos indexados)
- [ ] `/stats` mostra arquivos disponíveis
- [ ] `/test_send vip` funciona
- [ ] `/test_send free` funciona
- [ ] `/list_jobs` mostra jobs às 15h
- [ ] Logs do Render sem erros

---

## 🎉 PRONTO!

Se tudo estiver marcado acima, o sistema está funcionando!

**Agora é só aguardar até às 15h para o envio automático! 🚀**

---

## 📞 SUPORTE

Se tiver problemas:

1. Veja os logs do Render (Logs no menu lateral)
2. Rode `/stats` no bot
3. Verifique `CORRIGIR_ERRO_BANCO.md` para erros de conexão
4. Rode `python scan_local.py` localmente para debugar
