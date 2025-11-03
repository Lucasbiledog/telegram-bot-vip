# 📋 IDs dos Canais e Grupos - Sistema Bot Telegram

## ✅ IDs Corretos Atualizados

### 🎯 Canais de Envio Automático
```
CANAL VIP (destino):     -1003255098941
CANAL FREE (destino):    -1003246567304
```

### 📦 Grupo Fonte
```
GRUPO FONTE (arquivos):  -1003080645605
```

### 📝 Grupo de Logs
```
LOGS GROUP:              -5028443973
```

---

## 📍 Como Funcionam os Canais

### Canal VIP (-1003255098941)
- ✅ Recebe 1 arquivo aleatório **TODO DIA às 15h**
- ✅ Recebe **TODOS os arquivos** (sem limite de tamanho)
- ✅ Recebe arquivos com **"part1", "part2", "part3"**, etc.
- ✅ Recebe mensagem diária de renovação VIP com botão de pagamento

### Canal FREE (-1003246567304)
- ✅ Recebe 1 arquivo aleatório **TODA QUARTA-FEIRA às 15h**
- ⚠️ Apenas arquivos **até 500MB**
- ❌ **NÃO** recebe arquivos com "part", "parte", "pt1", "pt2", etc.
- ✅ Recebe mensagem diária promocional com botão de pagamento

### Grupo Fonte (-1003080645605)
- 📥 Grupo onde você adiciona TODOS os arquivos
- 🤖 Bot monitora 24/7 e indexa automaticamente
- 📊 Arquivos ficam disponíveis para sorteio

### Grupo de Logs (-5028443973)
- 📝 Recebe notificações de:
  - Arquivos indexados
  - Envios realizados
  - Erros e avisos do sistema

---

## 🔧 Configuração no .env

Adicione estas linhas no arquivo `.env`:

```env
# IDs dos Canais
VIP_CHANNEL_ID=-1003255098941
FREE_CHANNEL_ID=-1003246567304
LOGS_GROUP_ID=-5028443973
SOURCE_CHAT_ID=-1003080645605
```

---

## 🚀 No Render (Produção)

Configure as **Environment Variables**:

1. Acesse: Render Dashboard → Seu Serviço → **Environment**
2. Adicione:
   ```
   VIP_CHANNEL_ID = -1003255098941
   FREE_CHANNEL_ID = -1003246567304
   LOGS_GROUP_ID = -5028443973
   SOURCE_CHAT_ID = -1003080645605
   ```
3. Clique em **Save Changes**
4. Bot será reiniciado automaticamente

---

## ✅ Checklist de Configuração

- [x] Bot adicionado como **ADMIN** no Canal VIP (-1003255098941)
- [x] Bot adicionado como **ADMIN** no Canal FREE (-1003246567304)
- [x] Bot adicionado como **ADMIN** no Grupo Fonte (-1003080645605)
- [x] Bot adicionado no Grupo de Logs (-5028443973)
- [x] IDs configurados no arquivo `.env`
- [ ] IDs configurados no Render (Environment Variables)
- [ ] Bot testado com `/stats_auto`
- [ ] Scan inicial executado (`python scan_historico.py`)

---

## 🧪 Testar Sistema

Execute estes comandos no Telegram para testar:

```
/stats_auto          # Ver estatísticas do sistema
/listar_canais       # Ver todos os canais configurados
/gerar_url           # Gerar URLs de pagamento para descrições
```

---

## 📊 Horários de Envio

| Canal | Frequência | Horário | Timezone |
|-------|-----------|---------|----------|
| VIP   | Diário    | 15:00h  | America/Sao_Paulo |
| FREE  | Quartas   | 15:00h  | America/Sao_Paulo |

**Mensagens de Pagamento:**
- Ambos os canais recebem mensagem promocional **TODO DIA**
- Mensagens incluem botões com links de pagamento
- Não requer que o bot execute comandos nos canais

---

Última atualização: 03/11/2025
