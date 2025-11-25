# ⚡ TESTE RÁPIDO - Sistema de Pagamento

## 🎯 Objetivo
Testar se o sistema detecta pagamentos em crypto e ativa VIP automaticamente.

---

## 📝 PASSO 1: Configure a API Key

1. Abra o arquivo `.env`
2. Encontre a linha: `COINGECKO_API_KEY=COLE_SUA_API_KEY_AQUI`
3. Substitua por sua API key do CoinGecko
4. Salve o arquivo

---

## 🔧 PASSO 2: Instale Dependências (Se ainda não fez)

```powershell
python -m pip install -r requirements.txt
```

Aguarde terminar (pode demorar 2-5 minutos).

---

## ✅ PASSO 3: Verifique Configuração

```powershell
python check_config.py
```

**Resultado esperado**: Deve mostrar ✅ em tudo (alguns ⚠️ são OK).

---

## 💰 PASSO 4: Faça uma Transação de Teste

### 🧪 MODO TESTE ATIVO - Valores Reduzidos!

### Opção A: Usando BSC (BNB) - MAIS BARATO 💚

**Valor para teste**: ~0.0012 BNB (≈ $1 USD) ⭐

1. Abra sua carteira (Trust Wallet, MetaMask, etc)
2. Selecione **BNB Smart Chain**
3. Envie para: `0x40dDBD27F878d07808339F9965f013F1CBc2F812`
4. **COPIE O HASH DA TRANSAÇÃO** (importante!)

### Opção B: Usando Polygon (MATIC) - SUPER BARATO 💜

**Valor para teste**: ~0.4 MATIC (≈ $1 USD)

### Opção C: Outras blockchains

Qualquer das blockchains suportadas:
- Ethereum (ETH) - mais caro (evite para testes)
- Arbitrum, Optimism, Base - ok para testes

**Valor**: Equivalente a **$1-2 USD** (muito barato para testar!)

---

## 🧪 PASSO 5: Teste a Verificação

```powershell
python test_payment.py
```

No menu:
1. Digite **3** (Verificar transação)
2. Cole o hash da transação
3. Pressione Enter

**O sistema vai**:
- ✅ Procurar em todas blockchains
- ✅ Encontrar automaticamente
- ✅ Calcular valor em USD
- ✅ Determinar plano VIP
- ✅ Mostrar todos detalhes

**Exemplo de saída esperada (MODO TESTE)**:
```
✅ APROVADO
Mensagem: BNB nativo OK em BNB Smart Chain: $1.20
Valor em USD: $1.20
Plano: Mensal (30 dias)
Blockchain: BNB Smart Chain
Token: BNB
Quantidade: 0.001400
Confirmações: 5
```

---

## 🤖 PASSO 6: Teste no Bot (Opcional)

### 6.1 Inicie o bot

```powershell
python main.py
```

### 6.2 No Telegram, envie:

```
/tx <COLE_O_HASH_AQUI>
```

**Exemplo**:
```
/tx 0x1234567890abcdef...
```

**O bot deve responder (MODO TESTE)**:
```
✅ Pagamento confirmado: $1.20
VIP válido até 25/12/2025
Aguarde o convite do grupo VIP!
```

---

## 📊 Valores dos Planos

### 🧪 MODO TESTE (ATIVO AGORA)

| Valor Pago (USD) | Plano | Dias VIP |
|------------------|-------|----------|
| $1.00 - $1.99  | Mensal | 30 |
| $2.00 - $2.99 | Trimestral | 90 |
| $3.00 - $3.99 | Semestral | 180 |
| $4.00+ | Anual | 365 |

**🎯 Para testar, envie apenas $1-2 USD em crypto!**

### 💰 VALORES ORIGINAIS (PRODUÇÃO)

_Comentados no código, prontos para ativar:_

| Valor Pago (USD) | Plano | Dias VIP |
|------------------|-------|----------|
| $30.00 - $69.99  | Mensal | 30 |
| $70.00 - $109.99 | Trimestral | 90 |
| $110.00 - $178.99 | Semestral | 180 |
| $179.00+ | Anual | 365 |

---

## ❌ Problemas Comuns

### "Transação não encontrada"
- Aguarde 1-2 minutos (confirmações)
- Verifique o hash no explorador (bscscan.com)

### "Valor insuficiente"
- Envie pelo menos $1 USD (MODO TESTE ATIVO)
- Verifique se não tem taxas muito altas
- **Produção normal requer $30 USD**

### "Erro ao conectar"
- Verifique sua internet
- Tente novamente em 1 minuto

---

## 🎉 Sucesso!

Se o teste passou:
- ✅ Sistema funcionando 100%
- ✅ Pronto para uso real
- ✅ Pode colocar em produção

**Próximo passo**: Use o comando `/pagar` no bot para usuários reais!

---

## 💡 Dicas

1. **BSC é mais barato** para taxas (use para testes!)
2. **MODO TESTE: envie apenas $1-2** (super barato!)
3. **Guarde o hash** de cada transação
4. **Monitore logs** para debug
5. **Lembre de voltar aos valores de produção** quando terminar testes!

---

## 🆘 Precisa de Ajuda?

1. Execute: `python check_config.py`
2. Veja os logs do bot
3. Verifique se API key está correta
