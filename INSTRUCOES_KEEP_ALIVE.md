# 🚀 Como Manter o Bot Ativo 24/7 Gratuitamente (Sem Pagar Render)

## Problema
O plano gratuito do Render **hiberna o serviço após 15 minutos de inatividade**. Por isso o bot para após alguns dias sem uso.

## ✅ Solução Implementada: Sistema Interno de Keep-Alive

O bot agora possui um sistema interno que faz auto-ping a cada 10 minutos para se manter ativo.

### Configuração Necessária

1. **Configure a variável `SELF_URL` no Render:**
   - Vá em: Dashboard do Render → Seu serviço → Environment
   - Adicione a variável:
     ```
     SELF_URL=https://seu-bot.onrender.com
     ```
   - Substitua `seu-bot.onrender.com` pela URL real do seu serviço no Render

2. **Redeploy do serviço:**
   - Após adicionar a variável, faça um novo deploy
   - O sistema keep-alive iniciará automaticamente

## 🎯 Alternativa: Serviços Externos GRATUITOS de Keep-Alive

Caso prefira usar um serviço externo para fazer ping, aqui estão as melhores opções gratuitas:

### Opção 1: **Cron-Job.org** (Recomendado)
- **Site:** https://cron-job.org
- **Limite Gratuito:** 50 jobs, intervalo mínimo de 1 minuto
- **Como configurar:**
  1. Criar conta gratuita
  2. Criar novo Cronjob
  3. URL: `https://seu-bot.onrender.com/health`
  4. Intervalo: A cada 10 minutos
  5. Salvar

### Opção 2: **UptimeRobot**
- **Site:** https://uptimerobot.com
- **Limite Gratuito:** 50 monitores, checagem a cada 5 minutos
- **Como configurar:**
  1. Criar conta gratuita
  2. Add New Monitor
  3. Monitor Type: HTTP(s)
  4. URL: `https://seu-bot.onrender.com/health`
  5. Monitoring Interval: 5 minutes
  6. Salvar

### Opção 3: **BetterUptime**
- **Site:** https://betteruptime.com
- **Limite Gratuito:** Ilimitado, checagem a cada 3 minutos
- **Como configurar:**
  1. Criar conta gratuita
  2. Create Monitor
  3. URL: `https://seu-bot.onrender.com/health`
  4. Check frequency: 3 minutes
  5. Salvar

## 📊 Como Verificar se Está Funcionando

1. **Logs do Render:**
   - Vá em: Dashboard do Render → Seu serviço → Logs
   - Você verá mensagens como: `✅ Keep-alive ping OK [2025-10-01 15:30:00]`

2. **Endpoint de Health:**
   - Acesse: `https://seu-bot.onrender.com/health`
   - Deve retornar:
     ```json
     {
       "status": "healthy",
       "database": "healthy",
       "uptime_hours": 1.5,
       "timestamp": "2025-10-01T15:30:00"
     }
     ```

## 🔥 Por Que Isso Funciona?

- **Render hiberna após 15 minutos SEM requisições**
- **Keep-alive faz ping a cada 10 minutos**
- **Resultado:** Serviço NUNCA hiberna = Bot ativo 24/7

## ⚠️ Importante

- O sistema keep-alive INTERNO já está configurado e funcionará automaticamente
- Os serviços externos são OPCIONAIS (para backup/redundância)
- Ambos são 100% GRATUITOS
- Nenhuma mudança no código é necessária

## 🎁 Bônus: Alternativas ao Render (100% Gratuitas e Sem Hibernação)

Se ainda assim preferir não usar keep-alive, considere estas alternativas:

### **Railway.app**
- 500 horas grátis/mês
- Sem hibernação automática
- Fácil deploy via GitHub

### **Fly.io**
- Plano gratuito generoso
- Máquinas sempre ativas
- Deploy via CLI

### **Koyeb**
- 1 aplicação gratuita
- Sempre ativa
- Deploy via GitHub

---

**Resultado Final:** Bot funcionando **24 horas por dia, 7 dias por semana, 100% GRATUITO! 🎉**
