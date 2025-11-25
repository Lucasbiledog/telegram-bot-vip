# 📬 Mensagens Automáticas de Pagamento

## 🎯 Sistema de Notificação Automática

O bot agora **ENVIA AUTOMATICAMENTE** mensagens personalizadas no privado do usuário quando um pagamento é confirmado!

---

## ✅ Cenário 1: Pagamento com Convite Automático

**Quando**: Pagamento aprovado + Bot consegue gerar convite + Tem ID do usuário

**Mensagem enviada**:

```
🎉 PAGAMENTO CONFIRMADO!

✅ Valor recebido: $30.00 USD
👑 Plano ativado: Mensal (30 dias)
📅 Válido até: 25/12/2025

🔗 Clique no link abaixo para entrar no grupo VIP:
https://t.me/+ABC123XYZ

⚠️ IMPORTANTE: Este link expira em 2 horas e tem apenas 1 uso.

🎁 Seja bem-vindo(a) ao VIP!
💎 Aproveite todo o conteúdo exclusivo!
📬 Você receberá atualizações diárias de novos arquivos!

Obrigado pela confiança! 🙏
```

---

## ⚠️ Cenário 2: Pagamento sem Convite (Bot não é admin)

**Quando**: Pagamento aprovado + Bot NÃO consegue gerar convite

**Mensagem enviada**:

```
🎉 PAGAMENTO CONFIRMADO!

✅ Valor recebido: $30.00 USD
👑 Plano ativado: Mensal (30 dias)
📅 Válido até: 25/12/2025

⚠️ VIP ATIVADO COM SUCESSO!
📬 Entre em contato para receber o convite do grupo VIP.

🎁 Benefícios do seu plano:
• Acesso a conteúdo exclusivo premium
• Atualizações diárias de arquivos
• Suporte prioritário

Obrigado pela preferência! 🙏
```

---

## 📱 Cenário 3: Pagamento via Comando /tx

**Quando**: Usuário usa `/tx <hash>` no Telegram

**Mesmo comportamento**: Tenta gerar convite e envia mensagem formatada

---

## 🔄 Como Funciona:

### **Página de Checkout:**
1. Usuário paga e cola hash na página
2. Sistema valida pagamento on-chain
3. **Bot envia mensagem AUTOMATICAMENTE** no privado
4. Usuário recebe confirmação + convite (se disponível)

### **Comando /tx:**
1. Usuário envia `/tx <hash>` no Telegram
2. Bot valida pagamento
3. **Bot responde COM A MENSAGEM FORMATADA**
4. Inclui convite se conseguir gerar

---

## 🎨 Personalização por Plano:

| Plano | Nome Exibido | Dias |
|-------|--------------|------|
| $1-1.99 (teste) | Mensal | 30 |
| $2-2.99 (teste) | Trimestral | 90 |
| $3-3.99 (teste) | Semestral | 180 |
| $4+ (teste) | Anual | 365 |

**Produção**:
| Plano | Nome Exibido | Dias |
|-------|--------------|------|
| $30-69.99 | Mensal | 30 |
| $70-109.99 | Trimestral | 90 |
| $110-178.99 | Semestral | 180 |
| $179+ | Anual | 365 |

---

## ⚙️ Configurações Necessárias:

### **Para Convites Automáticos Funcionarem:**

1. ✅ Bot adicionado ao grupo VIP
2. ✅ Bot é **ADMINISTRADOR** do grupo
3. ✅ Bot tem permissão de **"Convidar usuários"**
4. ✅ `VIP_CHANNEL_ID` ou `GROUP_VIP_ID` configurado no `.env`

### **Se Não Configurado:**
- ✅ Mensagem ainda é enviada
- ⚠️ Sem convite automático
- ℹ️ Usuário é instruído a entrar em contato

---

## 📊 Logs Gerados:

```
INFO:payments:[NOTIFY] Mensagem de boas-vindas enviada para user 72293466
INFO:payments:[INVITE-DEBUG] Convite gerado: True
```

**Ou se falhar**:
```
WARNING:payments:[INVITE-DEBUG] Falha ao notificar usuário: Chat not found
```

---

## 🧪 Como Testar:

### **Teste 1: Pagamento via Página**
1. Faça pagamento de $1 (modo teste)
2. Cole hash na página de checkout
3. **Verifique seu privado no Telegram**
4. Deve receber mensagem automática!

### **Teste 2: Pagamento via Comando**
1. No Telegram, envie:
   ```
   /tx 0xSUA_HASH_AQUI
   ```
2. Bot responde com mensagem formatada
3. Se conseguir, inclui convite automático

---

## 💡 Benefícios:

✅ **Mensagens Profissionais**: Formatação HTML bonita
✅ **Informações Completas**: Valor, plano, validade
✅ **Convite Automático**: Link direto quando disponível
✅ **Boas-vindas Personalizadas**: Mensagem acolhedora
✅ **Instruções Claras**: Usuário sabe exatamente o que fazer
✅ **Fallback Inteligente**: Funciona mesmo sem convite automático

---

## 🔧 Troubleshooting:

### **Mensagem não chega**:
- Verifique se usuário iniciou conversa com bot (`/start`)
- Veja logs: `WARNING: Falha ao notificar usuário`
- Bot pode estar bloqueado pelo usuário

### **Sem convite automático**:
- Bot não é admin no grupo VIP
- `GROUP_VIP_ID` não configurado
- Permissões insuficientes

### **Formato quebrado**:
- Certifique-se que `parse_mode="HTML"` está sendo usado
- Logs devem mostrar: `[NOTIFY] Mensagem de boas-vindas enviada`

---

## 📝 Exemplo Real de Uso:

**Usuário paga $1 USD (teste) na BSC:**

1. Sistema detecta transação ✅
2. Valida valor: $1.00 USD ✅
3. Ativa plano: Mensal (30 dias) ✅
4. Tenta gerar convite... ⚠️ (falha: bot não é admin)
5. **Envia mensagem no privado**:

```
🎉 PAGAMENTO CONFIRMADO!

✅ Valor recebido: $1.00 USD
👑 Plano ativado: Mensal (30 dias)
📅 Válido até: 25/12/2025

⚠️ VIP ATIVADO COM SUCESSO!
📬 Entre em contato para receber o convite do grupo VIP.

🎁 Benefícios do seu plano:
• Acesso a conteúdo exclusivo premium
• Atualizações diárias de arquivos
• Suporte prioritário

Obrigado pela preferência! 🙏
```

**Usuário recebe e sabe que precisa entrar em contato!** ✅

---

**Sistema 100% funcional e profissional!** 🎉
