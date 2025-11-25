# 🔄 VOLTAR PARA VALORES DE PRODUÇÃO

## ⚠️ IMPORTANTE

Atualmente o sistema está em **MODO TESTE** com valores reduzidos:
- 30 dias VIP = **$1 USD** (teste)
- 90 dias VIP = **$2 USD** (teste)
- 180 dias VIP = **$3 USD** (teste)
- 365 dias VIP = **$4 USD** (teste)

## 🚀 Como Voltar para Produção

**⚠️ ATENÇÃO**: Você precisa editar **4 ARQUIVOS** para voltar à produção!

Quando terminar os testes e estiver pronto para aceitar pagamentos reais, siga TODOS estes passos:

---

### Passo 1: Editar `utils.py`

**Arquivo**: `F:\telegram_bot\bot-oficial\utils.py`

**Localização**: Linhas 29-53 (aproximadamente)

**O que fazer**:
1. **COMENTE** o bloco "MODO TESTE"
2. **DESCOMENTE** o bloco "VALORES ORIGINAIS"

**Resultado esperado**:
```python
# ====== MODO TESTE - VALORES REDUZIDOS ======
# Desativado - Use apenas para testes
# if amount_usd < 1.0:
#     return None
# elif amount_usd < 2.0:
#     return 30
# ...

# ====== VALORES ORIGINAIS (PRODUÇÃO) ======
# ATIVO - Valores reais para produção
if amount_usd < 30.0:
    return None
elif amount_usd < 70.0:
    return 30   # MENSAL
elif amount_usd < 110.0:
    return 90   # TRIMESTRAL
elif amount_usd < 179.0:
    return 180  # SEMESTRAL
else:
    return 365  # ANUAL
```

---

### Passo 2: Editar `main.py` (Função plan_from_amount)

**Arquivo**: `F:\telegram_bot\bot-oficial\main.py`

**Localização**: Linha ~4790 (função `plan_from_amount`)

**O que fazer**:
1. Procure por: `def plan_from_amount(amount_usd: float)`
2. **COMENTE** o bloco "MODO TESTE"
3. **DESCOMENTE** o bloco "VALORES ORIGINAIS"

**Resultado esperado**:
```python
def plan_from_amount(amount_usd: float) -> Optional[VipPlan]:
    # ====== MODO TESTE - VALORES REDUZIDOS ======
    # Desativado
    # if amount_usd < 1.00:
    #     return None
    # ...

    # ====== VALORES ORIGINAIS (PRODUÇÃO) ======
    # ATIVO
    if amount_usd < 30.00:
        return None
    elif amount_usd < 70.00:
        return VipPlan.MENSAL
    elif amount_usd < 110.00:
        return VipPlan.TRIMESTRAL
    elif amount_usd < 179.00:
        return VipPlan.SEMESTRAL
    else:
        return VipPlan.ANUAL
```

---

### Passo 3: Editar `main.py` (API /api/config)

**Arquivo**: `F:\telegram_bot\bot-oficial\main.py`

**Localização**: Linha ~7175 (função que retorna `/api/config`)

**O que fazer**:
1. Procure por: `value_tiers = {`
2. **COMENTE** o bloco "MODO TESTE"
3. **DESCOMENTE** o bloco "VALORES ORIGINAIS"

**Resultado esperado**:
```python
# ====== MODO TESTE - VALORES REDUZIDOS ======
# Desativado
# value_tiers = {
#     "30": 1.00,
#     "90": 2.00,
#     "180": 3.00,
#     "365": 4.00
# }

# ====== VALORES ORIGINAIS (PRODUÇÃO) ======
# ATIVO
value_tiers = {
    "30": 30.00,
    "90": 70.00,
    "180": 110.00,
    "365": 179.00
}
```

---

### Passo 4: Editar `webapp/app.js`

**Arquivo**: `F:\telegram_bot\bot-oficial\webapp\app.js`

**Localização**: Linha ~350 (função `loadBasicInfo`)

**O que fazer**:
1. Procure por: `const defaultPlans = {`
2. **COMENTE** o bloco "MODO TESTE"
3. **DESCOMENTE** o bloco "VALORES ORIGINAIS"

**Resultado esperado**:
```javascript
// ====== MODO TESTE - VALORES REDUZIDOS ======
// Desativado
// const defaultPlans = {
//   "30": 1.00,
//   "90": 2.00,
//   "180": 3.00,
//   "365": 4.00
// };

// ====== VALORES ORIGINAIS (PRODUÇÃO) ======
// ATIVO
const defaultPlans = {
  "30": 30.00,
  "90": 70.00,
  "180": 110.00,
  "365": 179.00
};
```

---

## ✅ Checklist - Use Buscar/Substituir

Para facilitar, você pode usar **Buscar e Substituir** em cada arquivo:

### 1. utils.py
**Buscar**: `# ====== MODO TESTE - VALORES REDUZIDOS ======`
**Nas próximas linhas**: Adicionar `#` no início

**Buscar**: `# ====== VALORES ORIGINAIS (PRODUÇÃO) ======`
**Nas próximas linhas**: Remover `#` do início

### 2. main.py (2 lugares!)
Repetir o mesmo processo DUAS vezes:
- Uma vez na função `plan_from_amount` (~linha 4790)
- Uma vez na função do `/api/config` (~linha 7175)

### 3. webapp/app.js
Mesmo processo do Python, mas em JavaScript.

---

## 🔄 Passo 5: Reiniciar o Sistema

Depois de editar TODOS os 4 lugares:

```powershell
# 1. Pare o bot (Ctrl+C)

# 2. Reinicie
python main.py

# Ou se usa auto_sender:
python auto_sender.py
```

---

## ✅ Passo 6: Verificar

Execute teste para confirmar:

```powershell
python test_payment.py
```

Escolha opção 2 (Testar cálculo de planos) e verifique se:
- $25.00 → Nenhum plano ✅
- $30.00 → Mensal (30 dias) ✅
- $70.00 → Trimestral (90 dias) ✅
- $110.00 → Semestral (180 dias) ✅
- $179.00 → Anual (365 dias) ✅

---

## 📊 Tabela de Valores (Produção)

| Valor Pago (USD) | Plano | Dias VIP |
|------------------|-------|----------|
| Menos de $30.00  | ❌ Insuficiente | 0 |
| $30.00 - $69.99  | ✅ Mensal | 30 |
| $70.00 - $109.99 | ✅ Trimestral | 90 |
| $110.00 - $178.99 | ✅ Semestral | 180 |
| $179.00+ | ✅ Anual | 365 |

---

## 📝 Resumo dos Arquivos

**Arquivos que precisam ser editados**:

1. ✅ `utils.py` (linha ~30)
2. ✅ `main.py` (linha ~4790) - função `plan_from_amount`
3. ✅ `main.py` (linha ~7175) - API `/api/config`
4. ✅ `webapp/app.js` (linha ~350) - página de checkout

**Total**: 4 edições em 3 arquivos

---

## 🎯 Pronto!

Seu sistema agora está configurado para produção com valores reais!

**Valores de Produção Ativos**:
- 30 dias VIP = $30.00 - $69.99
- 90 dias VIP = $70.00 - $109.99
- 180 dias VIP = $110.00 - $178.99
- 365 dias VIP = $179.00+

**⚠️ IMPORTANTE**: Teste com uma transação real de $30+ antes de divulgar!
