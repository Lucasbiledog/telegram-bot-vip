# 🧪 Guia de Teste do Sistema de Pagamentos

Este guia ajuda a testar o sistema de pagamento em criptomoedas do seu bot antes de colocá-lo em produção.

## 📋 Pré-requisitos

1. **Python 3.8+** instalado
2. **Dependências instaladas**: `pip install -r requirements.txt`
3. **Arquivo .env configurado** com as variáveis necessárias

## ⚙️ Configuração Atual

### Carteira de Pagamento
```
0x40dDBD27F878d07808339F9965f013F1CBc2F812
```

### Planos VIP
- **30 dias (Mensal)**: $30.00 - $69.99
- **90 dias (Trimestral)**: $70.00 - $109.99
- **180 dias (Semestral)**: $110.00 - $178.99
- **365 dias (Anual)**: $179.00+

### Blockchains Suportadas
- ✅ Ethereum (ETH)
- ✅ BNB Smart Chain (BSC)
- ✅ Polygon (MATIC)
- ✅ Arbitrum, Optimism, Base
- ✅ Avalanche, Fantom, Cronos
- E mais 15+ chains!

## 🚀 Como Testar

### Opção 1: Usar o Script de Teste (Recomendado)

Execute o script interativo:

```bash
python test_payment.py
```

O script oferece um menu com as seguintes opções:
1. **Mostrar configuração** - Exibe carteira, planos e chains suportadas
2. **Testar cálculo de planos** - Verifica se valores USD estão convertendo corretamente
3. **Verificar transação** - Testa uma transação real pela hash
4. **Fluxo completo** - Simula todo o processo de pagamento
5. **Todos os testes** - Executa tudo de uma vez

### Opção 2: Testar com o Bot

1. **Inicie o bot**:
   ```bash
   python main.py
   ```

2. **No Telegram, envie comandos**:
   ```
   /pagar - Ver instruções de pagamento
   /tx <hash> - Verificar uma transação
   ```

## 🧪 Casos de Teste

### Teste 1: Verificar Configuração

**Objetivo**: Confirmar que a carteira e chains estão configuradas

**Passos**:
1. Execute `python test_payment.py`
2. Escolha opção `1` (Mostrar configuração)
3. Verifique se a carteira está correta
4. Verifique se as blockchains estão listadas

**Resultado esperado**: ✅ Todas informações exibidas corretamente

---

### Teste 2: Cálculo de Planos

**Objetivo**: Verificar se valores USD estão convertendo para planos corretos

**Passos**:
1. Execute `python test_payment.py`
2. Escolha opção `2` (Testar cálculo de planos)
3. Observe os resultados

**Resultado esperado**: ✅ Todos os valores devem estar corretos:
- $25.00 → Nenhum plano (insuficiente)
- $30.00 → Mensal (30 dias)
- $70.00 → Trimestral (90 dias)
- $110.00 → Semestral (180 dias)
- $179.00 → Anual (365 dias)

---

### Teste 3: Verificar Transação Real

**Objetivo**: Testar verificação on-chain de uma transação

**Pré-requisitos**:
- Uma transação de teste enviada para a carteira configurada
- Hash da transação (exemplo: `0x1234...`)

**Passos**:
1. Faça uma transação de teste:
   - Envie **qualquer quantia** de crypto para: `0x40dDBD27F878d07808339F9965f013F1CBc2F812`
   - Pode ser em qualquer blockchain suportada
   - Copie o hash da transação

2. Execute `python test_payment.py`
3. Escolha opção `3` (Verificar transação)
4. Cole o hash da transação
5. Aguarde a verificação

**Resultado esperado**: ✅ O sistema deve:
- Encontrar a transação na blockchain correta
- Calcular o valor em USD
- Determinar o plano VIP apropriado
- Mostrar detalhes (token, quantidade, confirmações)

**Exemplo de saída**:
```
✅ APROVADO
Valor em USD: $30.50
Plano: Mensal (30 dias)
Blockchain: BNB Smart Chain
Token: BNB
Quantidade: 0.05
Confirmações: 12
```

---

### Teste 4: Fluxo Completo de Pagamento

**Objetivo**: Testar todo o processo de aprovação (sem enviar mensagens)

**Passos**:
1. Execute `python test_payment.py`
2. Escolha opção `4` (Fluxo completo)
3. Digite o hash da transação
4. Digite um ID de teste (ou deixe em branco)

**Resultado esperado**: ✅ O sistema deve:
- Verificar a transação
- Aprovar o pagamento
- Estender VIP no banco de dados
- Gerar convite (se bot estiver rodando)

---

## 🐛 Problemas Comuns

### Erro: "Transação não encontrada"

**Causas possíveis**:
- Hash incorreto ou mal formatado
- Transação ainda não confirmada na blockchain
- Transação em blockchain não suportada

**Solução**:
- Verifique o hash no explorador de blockchain (BSCScan, Etherscan, etc)
- Aguarde algumas confirmações
- Confirme que a blockchain é suportada

---

### Erro: "Preço USD indisponível"

**Causas possíveis**:
- API do CoinGecko com rate limiting
- Token não reconhecido
- Sem conexão com internet

**Solução**:
- O sistema usa preços de fallback automaticamente
- Configure `COINGECKO_API_KEY` no `.env` para evitar rate limits
- Verifique sua conexão

---

### Erro: "Valor insuficiente"

**Causa**: Transação menor que $30.00 USD

**Solução**: Envie uma transação de pelo menos $30.00 USD

---

## ✅ Checklist Antes de Produção

Antes de colocar o bot em produção, verifique:

- [ ] Carteira de pagamento está correta (`WALLET_ADDRESS` no `.env`)
- [ ] Bot Token está configurado (`BOT_TOKEN` no `.env`)
- [ ] ID do grupo VIP está correto (`VIP_CHANNEL_ID` no `.env`)
- [ ] Teste com transação real foi bem-sucedido
- [ ] Sistema detecta corretamente todas as blockchains
- [ ] Cálculo de planos está correto
- [ ] Preços USD estão sendo obtidos (ou usando fallback)
- [ ] Banco de dados está funcionando (VIP sendo registrado)

## 🆘 Suporte

Se encontrar problemas:

1. Verifique os logs do sistema
2. Execute `python test_payment.py` para diagnóstico
3. Revise as configurações no arquivo `.env`
4. Consulte a documentação das APIs:
   - [CoinGecko API](https://www.coingecko.com/api/documentation)
   - [Web3.py Docs](https://web3py.readthedocs.io/)

## 📊 Monitoramento em Produção

Após colocar em produção:

1. **Monitore os logs**: `tail -f logs/bot.log`
2. **Verifique transações pendentes**: Use `/listar_pendentes` (admin)
3. **Acompanhe aprovações**: Logs mostram todas aprovações automáticas
4. **Rate limiting**: Sistema usa cache e fallbacks automaticamente

## 🔐 Segurança

⚠️ **IMPORTANTE**:
- NUNCA compartilhe sua chave privada
- NUNCA commite o arquivo `.env` no Git
- Use endereços corretos (verifique múltiplas vezes)
- Teste com valores pequenos primeiro
- Mantenha backup do banco de dados

---

**Boa sorte com os testes! 🚀**
