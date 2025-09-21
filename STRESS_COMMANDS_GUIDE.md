# 🚀 Guia de Comandos de Stress Test via Telegram

Sistema integrado para executar testes de stress diretamente no Telegram e monitorar resultados nos logs do Render.

## 📋 **Comandos Disponíveis**

### **1. `/stress_quick`**
- **Função**: Teste rápido com 50 simulações
- **Duração**: ~10-15 segundos
- **Testa**: BSC, Ethereum, Polygon, tokens nativos e stablecoins
- **Logs**: Progresso a cada 10 testes

### **2. `/stress_connectivity`**
- **Função**: Teste de conectividade com 10 chains
- **Duração**: ~5 segundos
- **Testa**: Ethereum, BSC, Polygon, Arbitrum, Optimism, Base, Avalanche, Fantom, Cronos, Celo
- **Logs**: Status de cada chain individual

### **3. `/stress_tokens`**
- **Função**: Teste de diversidade de tokens
- **Duração**: ~8 segundos
- **Testa**: ETH, BNB, MATIC, USDC, USDT, AVAX, FTM, CRO
- **Logs**: Validação de preço para cada token

### **4. `/stress_status`**
- **Função**: Mostra status e instruções
- **Duração**: Instantâneo
- **Retorna**: Lista de comandos e como usar

## 🎯 **Como Usar**

### **Passo 1: Executar Comando**
1. Abra o Telegram
2. Vá para o chat do seu bot
3. Digite um dos comandos (ex: `/stress_quick`)
4. Pressione Enter

### **Passo 2: Monitorar Logs**
1. Acesse [Render Dashboard](https://dashboard.render.com)
2. Clique no seu serviço (telegram-bot-vip)
3. Vá na aba **"Logs"**
4. Procure por linhas com `[STRESS-TEST]`, `[CONNECTIVITY-TEST]`, ou `[TOKEN-TEST]`

### **Passo 3: Analisar Resultados**
- **Resumo**: Volta no Telegram automaticamente
- **Detalhes**: Disponíveis nos logs do Render
- **Tempo real**: Progresso aparece nos logs durante execução

## 📊 **Exemplos de Logs**

### **Teste Rápido (`/stress_quick`)**
```
🚀 [STRESS-TEST] Iniciando teste rápido com 50 simulações
📊 [STRESS-TEST] Progresso: 10/50 - Chain atual: BSC
📊 [STRESS-TEST] Progresso: 20/50 - Chain atual: Ethereum
📊 [STRESS-TEST] Progresso: 30/50 - Chain atual: Polygon
📊 [STRESS-TEST] Progresso: 40/50 - Chain atual: BSC
📊 [STRESS-TEST] Progresso: 50/50 - Chain atual: Ethereum
✅ [STRESS-TEST] Teste concluído!
📈 [STRESS-TEST] Duração: 12.34s
⚡ [STRESS-TEST] Throughput: 4.05 tests/s
🌐 [STRESS-TEST] Chains testadas: 3
```

### **Teste de Conectividade (`/stress_connectivity`)**
```
🌐 [CONNECTIVITY-TEST] Iniciando teste de conectividade
🔗 [CONNECTIVITY-TEST] Testando Ethereum (1/10)
✅ [CONNECTIVITY-TEST] Ethereum: 100.0ms
🔗 [CONNECTIVITY-TEST] Testando BSC (2/10)
✅ [CONNECTIVITY-TEST] BSC: 150.0ms
...
📊 [CONNECTIVITY-TEST] Resultado final:
   • Chains testadas: 10
   • Sucessos: 8
   • Taxa de sucesso: 80.0%
```

### **Teste de Tokens (`/stress_tokens`)**
```
💰 [TOKEN-TEST] Iniciando teste de diversidade de tokens
💎 [TOKEN-TEST] Testando ETH em Ethereum ($2.5)
✅ [TOKEN-TEST] ETH: Precisão 98.7%
💎 [TOKEN-TEST] Testando BNB em BSC ($1.8)
✅ [TOKEN-TEST] BNB: Precisão 98.9%
...
📊 [TOKEN-TEST] Resultado final:
   • Tokens testados: 8
   • Precisão média: 98.8%
   • Distribuição: {'low': 2, 'medium': 4, 'high': 2}
```

## 🔒 **Segurança**

### **Acesso Restrito**
- ✅ Apenas admins podem executar
- ✅ Usa sistema de verificação existente do bot
- ✅ Falha silenciosamente para não-admins

### **Proteção de Sistema**
- ✅ Não sobrecarrega o servidor
- ✅ Delays entre operações
- ✅ Timeouts configurados
- ✅ Não usa dados reais de transação

## 📈 **Interpretando Resultados**

### **No Telegram (Resumo)**
```
✅ Teste Rápido Concluído

📊 Resultados:
• Simulações: 50
• Duração: 12.34s
• Throughput: 4.05 tests/s
• Chains: 3

📝 Logs detalhados disponíveis no Render
```

### **No Render (Detalhes)**
- **Progresso em tempo real**
- **Métricas por chain**
- **Distribuição de tokens**
- **Tempos de resposta**
- **Taxas de sucesso**

## 🛠️ **Troubleshooting**

### **Comando não funciona**
- Verifique se você é admin
- Confirme que o bot está online
- Tente `/stress_status` primeiro

### **Logs não aparecem**
- Aguarde 5-10 segundos
- Atualize a página dos logs
- Procure por `[STRESS-TEST]` usando Ctrl+F

### **Bot não responde**
- Verifique se o bot está online no Render
- Confirme que o webhook está configurado
- Tente comando simples como `/start` primeiro

## 🎛️ **Configurações Avançadas**

### **Modificar Quantidade de Testes**
Edite `admin_stress_commands.py` linha 222:
```python
results = await render_stress.run_quick_payment_test(100)  # Era 50
```

### **Adicionar Novas Chains**
Edite `admin_stress_commands.py` linha 33:
```python
chains = [
    "Ethereum", "BSC", "Polygon", "Arbitrum", "Optimism",
    "Base", "Avalanche", "Fantom", "Cronos", "Celo",
    "zkSync Era"  # Nova chain
]
```

### **Modificar Delays**
Edite `admin_stress_commands.py` para ajustar velocidade:
```python
await asyncio.sleep(0.1)  # Diminuir para mais rápido
```

## 📱 **Fluxo Completo de Teste**

1. **Preparação**
   - Abra Telegram e Render em abas separadas
   - Vá nos logs do Render
   - Mantenha ambos visíveis

2. **Execução**
   - Digite `/stress_quick` no Telegram
   - Veja confirmação no Telegram
   - Monitore logs no Render em tempo real

3. **Análise**
   - Receba resumo no Telegram
   - Analise detalhes nos logs
   - Compare com testes anteriores

4. **Próximos Passos**
   - Use `/stress_connectivity` para validar chains
   - Use `/stress_tokens` para validar preços
   - Repita conforme necessário

## 🎉 **Vantagens desta Abordagem**

✅ **Controle remoto** via Telegram
✅ **Logs detalhados** no Render
✅ **Tempo real** de monitoramento
✅ **Sem dependências locais**
✅ **Integrado ao bot existente**
✅ **Seguro e restrito**

---

**Sistema pronto para uso! Execute `/stress_status` no bot para começar.** 🚀