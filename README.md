# Bot Telegram VIP - Sistema de Pagamento em Criptomoedas

Sistema automatizado de gerenciamento de VIP com pagamentos em criptomoedas.

## 📁 Estrutura de Arquivos

### Arquivos Principais
- **main.py** - Arquivo principal do bot
- **payments.py** - Sistema de processamento de pagamentos
- **utils.py** - Funções auxiliares
- **config.py** - Configurações
- **models.py** - Modelos de banco de dados
- **db.py** - Gerenciamento de banco de dados

### Sistemas de Performance
- **queue_system.py** - Sistema de filas
- **circuit_breaker.py** - Proteção contra falhas
- **batch_operations.py** - Operações em lote
- **cache.py** - Sistema de cache
- **rate_limiter.py** - Limitador de taxa
- **performance_monitor.py** - Monitor de performance

### Funcionalidades Especiais
- **auto_sender.py** - Envio automático de arquivos
- **scan_historico.py** - Scanner de histórico
- **scan_local.py** - Scanner local
- **optimized_vip_handler.py** - Gerenciador VIP otimizado
- **admin_stress_commands.py** - Comandos de teste de carga
- **keep_alive.py** - Mantém o bot ativo

## 🚀 Como Executar

### 1. Configurar .env
```env
BOT_TOKEN=seu_token_aqui
TELEGRAM_API_ID=seu_api_id
TELEGRAM_API_HASH=seu_api_hash
GROUP_VIP_ID=-1003255098941
WALLET_ADDRESS=0x40dDBD27F878d07808339F9965f013F1CBc2F812
```

### 2. Instalar Dependências
```bash
pip install -r requirements.txt
```

### 3. Executar o Bot
```bash
python main.py
```

## 💳 Sistema de Pagamento

O bot aceita pagamentos em múltiplas blockchains:
- Ethereum
- Binance Smart Chain (BSC)
- Polygon
- Arbitrum
- Avalanche
- E mais 20+ blockchains

### Planos VIP
- $30.00 - $69.99 = 30 dias (Mensal)
- $70.00 - $109.99 = 90 dias (Trimestral)
- $110.00 - $178.99 = 180 dias (Semestral)
- $179.00+ = 365 dias (Anual)

## 🔧 Funcionalidades

### Automáticas
- ✅ Detecção de pagamentos em blockchain
- ✅ Ativação automática de VIP
- ✅ Geração de convites para grupo VIP
- ✅ Envio de mensagem de boas-vindas no privado
- ✅ Gestão de expiração de VIP

### Comandos Admin
- `/tx <hash>` - Verificar transação manualmente
- `/listar_hashes` - Listar pagamentos
- `/excluir_hash <hash>` - Excluir pagamento

## 📝 Notas Importantes

- O bot precisa ser **administrador** no canal VIP
- Permissão **"Invite users via link"** é obrigatória
- Valores de teste configurados para facilitar testes
- Para produção, alterar valores em `utils.py`, `main.py` e `webapp/app.js`

## 🆘 Suporte

Para problemas ou dúvidas, verifique:
1. Configurações no arquivo `.env`
2. Permissões do bot no canal
3. Logs do sistema para erros

---

**Desenvolvido com Python + python-telegram-bot + Web3.py**
