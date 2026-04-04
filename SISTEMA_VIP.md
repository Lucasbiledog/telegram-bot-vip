# 📋 Sistema de Gerenciamento VIP

Sistema completo de gerenciamento de membros VIP com notificações automáticas, logs e expirações.

---

## 🎯 Funcionalidades Implementadas

### 1. ✅ **Mensagens no Privado Após Pagamento**

Quando um pagamento é confirmado, o bot **tenta enviar uma mensagem no privado** do usuário com:
- Confirmação do pagamento
- Detalhes do plano ativado
- Data de expiração
- Link para entrar no grupo VIP

**Se o usuário nunca iniciou conversa com o bot:**
- ❌ Mensagem não pode ser enviada (erro "Chat not found")
- 📝 Mensagem é **salva como pendente** no banco de dados
- ✅ **Quando o usuário ENTRAR no grupo VIP**, a mensagem é enviada automaticamente
- 📬 Também pode receber ao dar `/start` no bot

---

### 2. 📊 **Log de Membros Entrando/Saindo do Grupo**

O bot registra **automaticamente** todas as mudanças de membros:

| Evento | Descrição | Registro |
|--------|-----------|----------|
| ✅ **Joined** | Usuário entrou no grupo | Hora, nome, username, VIP até |
| 👋 **Left** | Usuário saiu voluntariamente | Hora, nome, username |
| 🚫 **Removed** | Usuário foi removido/kickado | Hora, nome, username |

**Os logs são enviados para:**
- 📊 **Grupo de Logs** (ID configurado em `LOGS_GROUP_ID`)
- 💾 **Banco de dados** (tabela `member_logs`)

**Comando para ver logs no banco:**
```
/logs          # Ver últimos 20 logs
/logs 50       # Ver últimos 50 logs
```

**Exemplo de log enviado no grupo:**
```
✅ JOINED
👤 João Silva (@joao123)
🆔 ID: 123456789
📅 25/01/2026 14:30:25
⏰ VIP até: 25/02/2026 14:30 (30 dias)
```

---

### 3. ⚠️ **Avisos de Expiração (5 Dias Antes)**

O bot verifica **a cada 6 horas** se há VIPs expirando em breve.

**Quando faltam 5 dias ou menos:**
- 📬 Usuário recebe mensagem privada avisando
- ⏰ Mostra exatamente quantos dias faltam
- 📅 Exibe data/hora exata da expiração
- 💎 Lembra de renovar o VIP

**Exemplo de mensagem:**
```
⚠️ AVISO DE EXPIRAÇÃO VIP

Olá! Seu acesso VIP está expirando em breve.

⏰ Expira em: 3 dia(s)
📅 Data de expiração: 29/01/2026 às 15:30

💎 Para renovar seu VIP, faça um novo pagamento!

Obrigado por fazer parte do nosso grupo VIP! 🙏
```

---

### 4. 🚫 **Remoção Automática ao Expirar**

Quando o VIP expira, o bot **automaticamente:**
1. Remove o usuário do grupo VIP
2. Atualiza o banco de dados (`is_vip = False`)
3. Cria registro no log de membros
4. Envia mensagem informando a expiração

**Exemplo de mensagem:**
```
⏰ VIP EXPIRADO

Seu acesso VIP expirou e você foi removido do grupo.

📅 Data de expiração: 25/01/2026 às 14:00

💎 Para renovar seu acesso VIP, faça um novo pagamento!

Obrigado por ter feito parte do nosso grupo! 🙏
```

---

## 📬 **Logs Enviados para Grupo**

Todos os eventos importantes são enviados automaticamente para o **Grupo de Logs** (configurado em `LOGS_GROUP_ID`):

| Evento | Quando | Informações |
|--------|--------|-------------|
| ✅ **Mensagem Enviada** | Pagamento aprovado + mensagem enviada | User, valor, plano, VIP até, link gerado |
| 📝 **Mensagem Pendente** | Mensagem não pode ser enviada | User, valor, plano, VIP até, motivo |
| 📬 **Pendente Enviada** | Usuário entra e recebe pendentes | User, quantidade de mensagens |
| ✅ **Joined** | Usuário entra no grupo | User, VIP até, dias restantes |
| 👋 **Left** | Usuário sai do grupo | User |
| 🚫 **Removed** | Usuário é removido | User, motivo |
| ⚠️ **Aviso Enviado** | 5 dias antes de expirar | User, dias restantes, data expiração |
| 🚫 **VIP Expirado** | VIP expira e usuário é removido | User, data expiração |

**Exemplo completo de fluxo:**
```
1. ✅ MENSAGEM DE BOAS-VINDAS ENVIADA
   👤 User: 123456789 (@joao)
   💰 Valor: $1.04 USD
   📅 Plano: Mensal (30 dias)
   ⏰ VIP até: 25/02/2026 14:30
   🔗 Link gerado: Sim

2. ✅ JOINED
   👤 João Silva (@joao)
   🆔 ID: 123456789
   📅 25/01/2026 14:30:25
   ⏰ VIP até: 25/02/2026 14:30 (30 dias)

3. ⚠️ AVISO DE EXPIRAÇÃO ENVIADO
   👤 User: 123456789 (@joao)
   ⏰ Expira em: 3 dia(s)
   📅 Data: 25/02/2026 14:30

4. 🚫 VIP EXPIRADO - USUÁRIO REMOVIDO
   👤 User: 123456789 (@joao)
   📅 Expirou em: 25/02/2026 14:30
   ❌ Removido do grupo VIP
```

---

## 🔧 Configuração

### Tabelas do Banco de Dados

O sistema cria automaticamente 2 novas tabelas:

#### **pending_notifications**
Armazena mensagens que não puderam ser enviadas:
- `id` - ID único
- `user_id` - ID do usuário no Telegram
- `username` - Username (opcional)
- `message` - Texto da mensagem (HTML)
- `created_at` - Quando foi criada
- `sent` - Se já foi enviada
- `sent_at` - Quando foi enviada

#### **member_logs**
Registra entrada/saída de membros:
- `id` - ID único
- `user_id` - ID do usuário
- `username` - Username (opcional)
- `first_name` - Nome do usuário
- `action` - "joined", "left" ou "removed"
- `vip_until` - Data de expiração do VIP
- `created_at` - Timestamp do evento

---

## 📝 Comandos Disponíveis

### Para Usuários:

**`/meu_vip`** - Verificar status do VIP
```
Mostra:
- Se tem VIP ativo
- Data de expiração
- Quantos dias faltam
- Alerta se está expirando em breve
```

### Para Admins:

**`/logs [quantidade]`** - Ver logs de membros
```
Exemplos:
/logs           → Últimos 20 logs
/logs 50        → Últimos 50 logs
/logs 100       → Últimos 100 logs (máximo)
```

---

## ⏰ Agendamentos Automáticos

| Job | Frequência | Horário | Função |
|-----|------------|---------|--------|
| **Verificação de Expirações** | A cada 6 horas | - | Verifica VIPs expirando e expirados |
| **Primeira Verificação** | 1 minuto após iniciar | - | Executa logo ao iniciar o bot |

---

## 🔄 Fluxo Completo

### Quando um usuário paga:

1. **Pagamento detectado** → Sistema valida transação
2. **VIP ativado** → Banco de dados atualizado
3. **Convite gerado** → Link para entrar no grupo
4. **Mensagem enviada:**
   - ✅ **Sucesso**: Usuário recebe no privado + **log enviado ao grupo**
   - ❌ **Falha**: Salva como pendente + **log enviado ao grupo**
5. **Usuário entra no grupo:**
   - ✅ **Log registrado** + **enviado ao grupo de logs**
   - 📬 **Mensagens pendentes enviadas automaticamente**
   - 📊 **Log de envio** enviado ao grupo
6. **5 dias antes** → Aviso de expiração enviado + **log enviado ao grupo**
7. **VIP expira** → Usuário removido + notificação + **log enviado ao grupo**

### ⚠️ Importante:
**NÃO é mais necessário dar /start!**
- Mensagens pendentes são enviadas **automaticamente** quando usuário **entra no grupo VIP**
- O bot captura o user_id ao entrar e envia todas as mensagens guardadas

---

## 📊 Exemplos de Logs

### Log de Entrada:
```
✅ JOINED: João Silva (@joao123)
    ID: 123456789
    📅 25/01/2026 14:30:25
    ⏰ VIP até: 25/02/2026 14:30
```

### Log de Saída:
```
👋 LEFT: Maria Santos (@maria)
    ID: 987654321
    📅 26/01/2026 10:15:00
```

### Log de Remoção:
```
🚫 REMOVED: Pedro Costa (@pedro)
    ID: 555666777
    📅 27/01/2026 09:00:00
    ⏰ VIP até: 27/01/2026 08:59 (expirado)
```

---

## 🐛 Troubleshooting

### Mensagens não estão sendo enviadas no privado:
✅ **Solução**: Usuário precisa dar `/start` no bot primeiro

### Usuário não foi removido ao expirar:
- Verificar logs: procure por `[EXPIRATION]`
- Bot precisa ser **administrador** no grupo
- Bot precisa ter permissão de **"Ban users"**

### Logs não aparecem com /logs:
- Apenas **owner** pode ver logs (configurado em `OWNER_ID`)
- Verificar se `OWNER_ID` está correto no `.env`

### Avisos de expiração não estão sendo enviados:
- Verificar se job está rodando: procure por `[EXPIRATION-CHECK]` nos logs
- Job roda a cada 6 horas
- Primeira execução é 1 minuto após iniciar

---

## 📁 Arquivos Relacionados

- **`vip_manager.py`** - Sistema completo de gerenciamento VIP
- **`models.py`** - Modelos PendingNotification e MemberLog
- **`payments.py`** - Integração com sistema de pagamentos
- **`main.py`** - Handlers e jobs registrados

---

## 🚀 Para Ativar

1. **Reinicie o bot:**
   ```bash
   python main.py
   ```

2. **Verifique nos logs:**
   ```
   ✅ Sistema de log de membros ativado
   ✅ Comandos /logs e /meu_vip registrados
   ✅ Sistema de verificação de expirações VIP ativado (a cada 6 horas)
   ```

3. **Teste:**
   - Faça um pagamento de teste
   - Verifique se mensagem é enviada ou salva como pendente
   - Dê `/start` para receber mensagens pendentes
   - Use `/meu_vip` para ver seu status
   - (Admin) Use `/logs` para ver registros

---

**✨ Sistema completo e funcionando!**
