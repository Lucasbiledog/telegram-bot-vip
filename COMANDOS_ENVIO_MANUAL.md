# 📤 COMANDOS DE ENVIO MANUAL DO BOT

## 🎯 Principais Comandos de Envio

### 1. `/enviar_pack_agora` ⭐ (MAIS USADO)

**Descrição**: Envia pack VIP ou FREE imediatamente (ignora horário agendado)

**Uso**:
```
/enviar_pack_agora vip
/enviar_pack_agora free
```

**O que faz**:
- ✅ Envia próximo pack disponível da fila
- ✅ Funciona mesmo fora do horário agendado
- ✅ Marca pack como enviado automaticamente
- ✅ Envia para canal correto (VIP ou FREE)

**Quando usar**:
- Quer enviar um pack agora sem esperar o horário
- Precisa testar envio de packs
- Fazer envio manual urgente

---

### 2. `/test_send` (Para Testes)

**Descrição**: Testa envio com debug detalhado

**Uso**:
```
/test_send vip
/test_send free
```

**O que faz**:
- ✅ Mostra estatísticas antes do envio
- ✅ Envia pack
- ✅ Mostra estatísticas depois
- ✅ Útil para debug

**Quando usar**:
- Testar se sistema de envio está funcionando
- Ver detalhes de estatísticas
- Debugging

---

### 3. `/say_vip` e `/say_free` (Mensagens)

**Descrição**: Envia mensagem de texto para canal

**Uso**:
```
/say_vip Olá membros VIP! Novo pack chegando!
/say_free Olá pessoal, confira o preview!
```

**O que faz**:
- ✅ Envia mensagem de texto simples
- ✅ Para canal VIP ou FREE
- ✅ Suporta formatação Markdown/HTML

**Quando usar**:
- Enviar avisos
- Comunicados
- Mensagens personalizadas

---

## 📦 Comandos de Gerenciamento de Packs

### 4. `/listar_packs`

**Descrição**: Lista todos os packs (VIP e FREE) com status

**Uso**:
```
/listar_packs
```

**Mostra**:
- 📋 Todos packs cadastrados
- ✅ Status (ENVIADO ou PENDENTE)
- 📷 Quantidade de previews
- 📄 Quantidade de arquivos
- 📅 Data de criação
- ⏰ Horários de envio agendados

---

### 5. `/pack_info`

**Descrição**: Informações detalhadas de um pack específico

**Uso**:
```
/pack_info 123
```
(Onde 123 é o ID do pack da lista)

**Mostra**:
- 📦 Título do pack
- 📊 Estatísticas
- 📁 Lista de arquivos
- 🕐 Horário de criação

---

### 6. `/set_pendentevip` e `/set_pendentefree`

**Descrição**: Marca pack como PENDENTE (não enviado)

**Uso**:
```
/set_pendentevip 123
/set_pendentefree 456
```

**O que faz**:
- ✅ Marca pack como não enviado
- ✅ Permite reenviar pack
- ✅ Útil se envio falhou

---

### 7. `/set_enviadovip` e `/set_enviadofree`

**Descrição**: Marca pack como ENVIADO (manualmente)

**Uso**:
```
/set_enviadovip 123
/set_enviadofree 456
```

**O que faz**:
- ✅ Marca pack como enviado
- ✅ Remove da fila de envio
- ✅ Útil para ajustes manuais

---

## 🔄 Comandos de Automação

### 8. `/set_pack_horario_vip` e `/set_pack_horario_free`

**Descrição**: Define horário de envio automático

**Uso**:
```
/set_pack_horario_vip 09:00
/set_pack_horario_free 10:30
```

**O que faz**:
- ✅ Define horário diário para envio VIP
- ✅ Define horário semanal para envio FREE
- ✅ Formato 24h: HH:MM

---

### 9. `/listar_jobs`

**Descrição**: Lista todos os trabalhos agendados

**Uso**:
```
/listar_jobs
```

**Mostra**:
- 📅 Jobs ativos
- ⏰ Horários configurados
- 🔄 Próximas execuções

---

## 🧪 Comandos de Teste/Debug

### 10. `/simularvip` e `/simularfree`

**Descrição**: Simula envio sem realmente enviar

**Uso**:
```
/simularvip
/simularfree
```

**O que faz**:
- ✅ Mostra o que seria enviado
- ✅ Não envia de verdade
- ✅ Útil para testar antes

---

### 11. `/stats_auto`

**Descrição**: Mostra estatísticas do auto sender

**Uso**:
```
/stats_auto
```

**Mostra**:
- 📊 Total de arquivos indexados
- ✅ Disponíveis para VIP/FREE
- 📤 Total enviado
- 📈 Estatísticas gerais

---

### 12. `/check_files`

**Descrição**: Verifica arquivos disponíveis

**Uso**:
```
/check_files
```

**O que faz**:
- ✅ Verifica arquivos no sistema
- ✅ Mostra disponibilidade
- ✅ Debug de problemas

---

## 📋 Comandos de Histórico

### 13. `/scan_history`

**Descrição**: Escaneia histórico de grupo fonte

**Uso**:
```
/scan_history
```

**O que faz**:
- 🔄 Busca novos arquivos no grupo fonte
- 📥 Indexa arquivos encontrados
- 📊 Atualiza banco de dados

---

### 14. `/scan_full`

**Descrição**: Escaneamento completo do histórico

**Uso**:
```
/scan_full
```

**O que faz**:
- 🔄 Escaneia TODO histórico
- ⚠️ Pode demorar muito
- 📥 Indexa tudo desde o início

---

## 🎯 RESUMO RÁPIDO

Para **enviar pack AGORA** (mais comum):
```
/enviar_pack_agora vip
/enviar_pack_agora free
```

Para **ver o que tem na fila**:
```
/listar_packs
```

Para **enviar mensagem simples**:
```
/say_vip Sua mensagem aqui
/say_free Sua mensagem aqui
```

Para **testar sistema**:
```
/test_send vip
/stats_auto
```

---

## ⚠️ IMPORTANTE

**Comandos de ADMIN apenas**:
- Todos estes comandos só funcionam para admins configurados
- Configure admins com: `/add_admin USER_ID`
- Ver admins: `/listar_admins`

**Permissões do Bot**:
- Bot precisa ter permissão de ADMIN nos canais
- Permissão para enviar mensagens/arquivos
- Permissão para ler histórico (se usar scan)

---

## 📞 Outros Comandos Úteis

### Gerenciamento de Mensagens Automáticas:
```
/add_msg_vip - Adicionar mensagem VIP
/add_msg_free - Adicionar mensagem FREE
/list_msgs_vip - Listar mensagens VIP
/list_msgs_free - Listar mensagens FREE
/toggle_msg_vip ID - Ativar/desativar mensagem
/del_msg_vip ID - Deletar mensagem
```

### Informações:
```
/getid - Ver seu ID
/chat_info - Info do chat atual
/get_chat_id - ID do chat
/listar_canais - Listar canais configurados
```

### VIP/Pagamentos:
```
/pagar - Instruções de pagamento
/tx HASH - Verificar transação
/listar_vips - Listar membros VIP
/vip_addtime USER_ID DIAS - Adicionar tempo VIP
```

---

## 🚀 Workflow Típico

1. **Ver packs disponíveis**:
   ```
   /listar_packs
   ```

2. **Enviar pack agora**:
   ```
   /enviar_pack_agora vip
   ```

3. **Verificar se enviou**:
   ```
   /stats_auto
   ```

4. **Enviar mensagem de aviso**:
   ```
   /say_vip Novo pack disponível! Aproveitem 🔥
   ```

---

**Todos os comandos estão prontos para uso! 🎉**

Para ver lista completa de TODOS comandos: `/comandos` ou `/listar_comandos`
