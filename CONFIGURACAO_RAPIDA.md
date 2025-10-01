# ⚡ Configuração Rápida - Bot Ativo 24/7 GRÁTIS

## 🎯 PASSO A PASSO (5 minutos)

### 1️⃣ Configure a Variável no Render

```bash
# Vá no painel do Render:
# https://dashboard.render.com

# Navegue até: Seu serviço → Environment

# Adicione esta variável:
SELF_URL=//https://telegram-bot-vip-hfn7.onrender.com

# ⚠️ IMPORTANTE: Substitua "seu-bot" pela URL real do seu serviço!
```

### 2️⃣ Faça o Deploy

```bash
# No painel do Render, clique em "Manual Deploy"
# Ou faça push no GitHub (se configurado auto-deploy)
```

### 3️⃣ Verifique nos Logs

```bash
# Vá em: Logs
# Você verá:
✅ Sistemas de alta performance inicializados com sucesso
🔄 Sistema Keep-Alive iniciado (ping a cada 10 minutos)
```

## ✅ PRONTO! Bot ativo 24/7

---

## 🔍 Como Encontrar a URL do Seu Bot no Render

1. **Dashboard do Render** → Seu serviço
2. No topo verá algo como:
   ```
   https://telegram-bot-xxxx.onrender.com
   ```
3. Copie essa URL e use como `SELF_URL`

---

## 🧪 Testar se Está Funcionando

### Método 1: Abrir no Navegador
```
https://seu-bot.onrender.com/health
```

Deve retornar:
```json
{
  "status": "healthy",
  "database": "healthy"
}
```

### Método 2: Ver Logs a Cada 10 Minutos
```
✅ Keep-alive ping OK [2025-10-01 15:30:00]
✅ Keep-alive ping OK [2025-10-01 15:40:00]
✅ Keep-alive ping OK [2025-10-01 15:50:00]
```

---

## 🎁 EXTRA: Backup com Serviço Externo (Opcional)

Para máxima confiabilidade, configure também um serviço externo:

### Recomendado: Cron-Job.org

1. Acesse: https://cron-job.org/en/signup.php
2. Crie conta (grátis)
3. Clique em "Create cronjob"
4. Preencha:
   - **Title:** Bot Telegram Keep-Alive
   - **URL:** `https://seu-bot.onrender.com/health`
   - **Schedule:** Every 10 minutes
5. Salvar

**Pronto! Redundância dupla = 99.9% uptime garantido!**

---

## ❓ Problemas Comuns

### "SELF_URL não configurada"
- **Solução:** Adicione a variável `SELF_URL` no Render e faça redeploy

### "Keep-alive ping falhou"
- **Solução:** Verifique se a URL está correta e se o serviço está rodando

### Bot ainda hiberna
- **Solução:**
  1. Verifique os logs para confirmar que o keep-alive está rodando
  2. Configure um serviço externo (Cron-Job.org) como backup

---

## 💰 Custo Total: R$ 0,00 (GRÁTIS) 🎉
