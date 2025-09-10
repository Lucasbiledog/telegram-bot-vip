# 🤖 Bot Telegram - Sempre Ativo

Este bot foi configurado para ficar **sempre ativo** e se recuperar automaticamente de falhas.

## 🚀 Como Iniciar o Bot

### Opção 1: Script Supervisor (Recomendado)
Execute o supervisor que reinicia o bot automaticamente em caso de falhas:

```bash
# Windows
start_bot.bat

# Ou manualmente
python start_bot.py
```

### Opção 2: Execução Direta
```bash
python main.py
```

## 🛡️ Recursos de Estabilidade

### 1. **Supervisor Automático** (`start_bot.py`)
- ✅ Reinicia automaticamente em caso de falha
- ✅ Limite de 10 reinicializações por hora (proteção contra loop)
- ✅ Log detalhado de falhas e reinicializações
- ✅ Monitoramento constante do processo

### 2. **Tratamento de Sinais** (`main.py`)
- ✅ Ignora Ctrl+C (SIGINT) - continua executando
- ✅ Ignora SIGTERM - continua executando
- ✅ Log de sinais recebidos
- ✅ Tratamento robusto de timeouts

### 3. **Inicialização Robusta**
- ✅ 3 tentativas de inicialização com timeout de 60s cada
- ✅ Aguarda 10 segundos entre tentativas
- ✅ Continua funcionando mesmo se bot do Telegram falhar
- ✅ Retry automático para configuração de webhook

### 4. **Tratamento de Erros**
- ✅ Captura exceções em todas as operações críticas
- ✅ Log detalhado de erros com tipo e resposta
- ✅ Fallbacks para operações que podem falhar
- ✅ Não interrompe execução por erros pontuais

## 📊 Monitoramento

### Logs do Supervisor
```
bot_supervisor.log  # Log do sistema de supervisão
```

### Logs da Aplicação
- Saída padrão do terminal
- Logs do uvicorn (servidor web)
- Logs do bot Telegram

## 🔧 Como Parar o Bot

### Para o Supervisor:
- **Windows**: Feche a janela do terminal
- **Ctrl+C duas vezes**: Primeira ignora, segunda para definitivamente
- **Task Manager**: Termine o processo Python

### Para Execução Direta:
- Feche o terminal
- Task Manager

## ⚠️ Troubleshooting

### Bot não responde?
1. Verifique se `BOT_TOKEN` está correto no `.env`
2. Verifique se `WEBHOOK_URL` está acessível
3. Confira os logs para erros específicos

### Muitos reinicializações?
- O supervisor limita a 10 restarts por hora
- Aguarde 1 hora ou corrija o problema raiz

### Como atualizar o código?
1. Pare o supervisor (Ctrl+C duas vezes)
2. Atualize os arquivos
3. Reinicie: `python start_bot.py`

## 🔄 Auto-Restart

O bot agora tem **3 níveis** de proteção contra falhas:

1. **Nível 1**: Tratamento interno de erros (não para o bot)
2. **Nível 2**: Captura de sinais (ignora Ctrl+C)
3. **Nível 3**: Supervisor externo (reinicia processo completo)

## 📈 Status de Funcionamento

Para verificar se está funcionando:
- ✅ Bot responde aos comandos
- ✅ `/keepalive` a cada 4 minutos no log
- ✅ Mensagens de "Bot conectado" no log
- ✅ Webhook recebendo requests

---

**🚀 O bot agora está configurado para funcionar 24/7 com máxima estabilidade!**