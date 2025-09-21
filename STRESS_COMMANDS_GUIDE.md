# 🚀 Guia de Comandos de Stress Test via Telegram

Sistema integrado para executar testes de stress diretamente no Telegram e monitorar resultados nos logs do Render.

## 📋 **Comandos Disponíveis**

### **🎯 Testes de Pagamento VIP (NOVOS!)**

### **1. `/test_vip_payments`**
- **Função**: **Teste completo de pagamentos VIP reais**
- **Duração**: ~60-90 segundos
- **Testa**: 13+ moedas da página de checkout (ETH, BNB, MATIC, USDC, USDT, BTCB, AVAX, etc.)
- **Cenários**: 50 simulações de assinatura VIP com valores reais ($0.05-$10)
- **Logs**: Distribuição de moedas, planos VIP, performance por chain
- **🎯 RECOMENDADO: Use este para validar o sistema de pagamentos completo**

### **2. `/test_vip_quick`**
- **Função**: Teste rápido de pagamentos VIP
- **Duração**: ~30 segundos
- **Testa**: Principais moedas (ETH, BNB, USDC, USDT)
- **Cenários**: 20 simulações focadas
- **Logs**: Resumo de performance

### **🔧 Testes de Sistema Geral**

### **3. `/stress_quick`**
- **Função**: Teste rápido de sistema
- **Duração**: ~10-15 segundos
- **Testa**: BSC, Ethereum, Polygon, tokens nativos e stablecoins
- **Logs**: Progresso a cada 10 testes

### **4. `/stress_connectivity`**
- **Função**: Teste de conectividade com 10 chains
- **Duração**: ~5 segundos
- **Testa**: Ethereum, BSC, Polygon, Arbitrum, Optimism, Base, Avalanche, Fantom, Cronos, Celo
- **Logs**: Status de cada chain individual

### **5. `/stress_tokens`**
- **Função**: Teste de diversidade de tokens
- **Duração**: ~8 segundos
- **Testa**: ETH, BNB, MATIC, USDC, USDT, AVAX, FTM, CRO
- **Logs**: Validação de preço para cada token

### **6. `/stress_status`**
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

### **Teste de Pagamentos VIP (`/test_vip_payments`)**
```
🎯 [VIP-PAYMENT-TEST] Iniciando teste abrangente de pagamentos VIP
   • Total de testes: 50
   • Moedas principais: 13
   • Faixas VIP: 4

💰 [VIP-PAYMENT-TEST] Distribuição de moedas:
   • USDC: 8 testes
   • ETH: 7 testes
   • BNB: 6 testes
   • USDT: 5 testes
   • MATIC: 4 testes

📦 [VIP-PAYMENT-TEST] Distribuição de planos VIP:
   • PREMIUM (180 dias): 15 testes
   • ANNUAL (365 dias): 12 testes
   • STANDARD (60 dias): 11 testes
   • BASIC (30 dias): 12 testes

🔄 [VIP-PAYMENT-TEST] Batch 1/10 (5 testes)
   ✅ USDC (Ethereum) - $1.85 USD - 0.234s
   ✅ ETH (Ethereum) - $2.50 USD - 0.198s
   ✅ BNB (BSC) - $1.20 USD - 0.245s
   ✅ USDT (BSC) - $3.00 USD - 0.221s
   ✅ MATIC (Polygon) - $1.75 USD - 0.189s

🎉 [VIP-PAYMENT-TEST] Teste concluído!
📊 [VIP-PAYMENT-TEST] Resultados finais:
   • Taxa de sucesso: 96.0% (48/50)
   • Tempo médio: 0.215s
   • Throughput: 12.3 testes/s
   • Duração total: 45.67s

💰 [VIP-PAYMENT-TEST] Performance por moeda:
   • USDC     |  8/ 8 (100.0%) | 0.201s | $14.20
   • ETH      |  7/ 7 (100.0%) | 0.198s | $17.50
   • BNB      |  6/ 6 (100.0%) | 0.243s | $7.20
   • USDT     |  5/ 5 (100.0%) | 0.221s | $12.50
   • MATIC    |  4/ 4 (100.0%) | 0.189s | $7.00

🌐 [VIP-PAYMENT-TEST] Performance por chain:
   • Ethereum    |  15/15 (100.0%)
   • BSC         |  11/11 (100.0%)
   • Polygon     |   4/ 4 (100.0%)
   • Arbitrum    |   3/ 3 (100.0%)
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