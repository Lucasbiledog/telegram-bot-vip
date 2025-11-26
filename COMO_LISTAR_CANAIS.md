# 📋 Como Listar Canais/Grupos do Bot

Este guia explica como usar o script para listar todos os canais e grupos onde seu bot está presente.

---

## 🎯 Para Que Serve

O script **listar_canais_bot.py** faz o seguinte:

1. **Conecta** na sua conta do Telegram usando Pyrogram
2. **Lista** todos os canais/grupos onde o bot está presente
3. **Identifica** se o bot é administrador ou apenas membro
4. **Mostra** o ID de cada canal (para configurar no `.env`)
5. **Exibe** as permissões do bot (modo detalhado)

---

## ✅ Pré-requisitos

Antes de executar, certifique-se de que:

1. ✅ O arquivo `.env` está configurado com:
   - `API_ID`
   - `API_HASH`
   - `BOT_TOKEN`

2. ✅ O Pyrogram está instalado:
   ```bash
   py -m pip install pyrogram
   ```

3. ✅ O bot foi adicionado aos canais/grupos que você quer listar

---

## 🚀 Como Executar

### Método 1: Arquivo Batch (Mais Fácil - Windows)

1. Clique duas vezes em **`listar_canais.bat`**
2. Escolha a opção desejada:
   - **1** - Listagem simples (rápida)
   - **2** - Listagem detalhada com permissões (mais lenta)
3. Aguarde o resultado

### Método 2: Linha de Comando

```bash
# No terminal/prompt
py listar_canais_bot.py
```

---

## 📊 Opções Disponíveis

### Opção 1: Listagem Simples (Rápida)

Mostra:
- Nome do canal/grupo
- ID do canal (para usar no `.env`)
- Tipo (SUPERGROUP, GROUP, CHANNEL)
- Username (se tiver)
- Status do bot (ADMINISTRATOR, MEMBER, etc.)

**Exemplo de saída:**
```
👑 BOT É ADMINISTRADOR:
──────────────────────────────────────────

[1] Grupo VIP Premium
    ID: -1003255098941
    Tipo: SUPERGROUP
    Username: Sem username
    Status do Bot: ADMINISTRATOR

[2] Canal de Anúncios
    ID: -1001234567890
    Tipo: CHANNEL
    Username: @meu_canal
    Status do Bot: ADMINISTRATOR
```

### Opção 2: Listagem Detalhada (Mais Lenta)

Mostra tudo da opção 1, MAIS:
- ✅/❌ Can Invite Users (necessário para gerar convites!)
- ✅/❌ Can Delete Messages
- ✅/❌ Can Restrict Members
- ✅/❌ Can Promote Members
- ✅/❌ Can Change Info
- ✅/❌ Can Post Messages (canais)

**Exemplo de saída:**
```
👑 [1] Grupo VIP Premium
    ID: -1003255098941
    Tipo: SUPERGROUP
    Username: Sem username
    Status do Bot: ADMINISTRATOR
    Permissões:
      ✅ Invite Users
      ✅ Delete Messages
      ❌ Restrict Members
      ❌ Promote Members
      ✅ Change Info
```

---

## 🔑 Como Usar os IDs

Depois de listar os canais, **copie o ID** do canal desejado e configure no `.env`:

```env
# Exemplo: ID do Grupo VIP
GROUP_VIP_ID=-1003255098941

# Ou para canal de anúncios
VIP_CHANNEL_ID=-1001234567890
```

**⚠️ IMPORTANTE:**
- Use canais onde o bot é **ADMINISTRADOR**
- O bot precisa da permissão **"Invite Users"** para gerar convites
- IDs de grupos/canais sempre começam com `-100`

---

## 🔐 Autenticação

Na **primeira execução**, o script vai pedir:

1. **Número de telefone** (formato internacional: +5511999999999)
2. **Código de confirmação** (enviado pelo Telegram)
3. **Senha 2FA** (se você tiver ativado)

Após a primeira vez, o script salvará uma sessão (`my_account.session`) e não pedirá novamente.

---

## ❓ Problemas Comuns

### "Nenhum canal/grupo encontrado"

**Possíveis causas:**
- Bot não foi adicionado aos canais
- Você conectou com a conta errada
- Canais/grupos foram deletados

**Solução:**
1. Adicione o bot aos canais desejados
2. Certifique-se de estar usando a conta correta
3. Verifique se os canais ainda existem

### "API_ID, API_HASH ou BOT_TOKEN não configurados"

**Solução:**
1. Abra o arquivo `.env`
2. Certifique-se de que estas linhas existem:
   ```env
   API_ID=12345678
   API_HASH=abc123def456...
   BOT_TOKEN=7000811352:AAH...
   ```

### "FloodWait: A wait of X seconds is required"

**Causa:** Telegram está limitando requisições (muitos chats)

**Solução:**
- Aguarde o tempo indicado
- Use a opção 1 (listagem simples) que é mais rápida

### Erro ao conectar com Pyrogram

**Solução:**
1. Delete o arquivo `my_account.session`
2. Execute o script novamente
3. Faça login novamente

---

## 🎯 Casos de Uso

### 1. Descobrir ID do Grupo VIP

```bash
# Execute o script
py listar_canais_bot.py

# Escolha opção 1
# Procure pelo nome do grupo
# Copie o ID que começa com -100
# Cole no .env como GROUP_VIP_ID
```

### 2. Verificar Permissões do Bot

```bash
# Execute o script
py listar_canais_bot.py

# Escolha opção 2
# Verifique se "Invite Users" está ✅
# Se estiver ❌, dê essa permissão ao bot no Telegram
```

### 3. Auditar Onde o Bot Está

```bash
# Use opção 1 para ver rapidamente
# Todos os canais/grupos onde o bot foi adicionado
```

---

## 💡 Dicas

1. **Listagem rápida**: Use opção 1 se só precisa dos IDs
2. **Verificar permissões**: Use opção 2 antes de configurar GROUP_VIP_ID
3. **Salve os IDs**: Anote os IDs importantes em um arquivo de texto
4. **Bot como admin**: Sempre configure o bot como ADMINISTRADOR nos canais importantes

---

## 📝 Logs e Debug

O script mostra mensagens detalhadas:
- ✅ Sucesso em verde
- ⚠️ Avisos em amarelo
- ❌ Erros em vermelho
- 📊 Informações em azul

Se tiver erros, leia as mensagens com atenção - elas explicam o problema.

---

## 🔄 Próximos Passos

Depois de listar os canais:

1. Copie o **ID do grupo VIP**
2. Configure no `.env`:
   ```env
   GROUP_VIP_ID=-1003255098941
   ```
3. Reinicie o bot:
   ```bash
   py main.py
   ```
4. Teste o sistema de pagamento

---

## 🆘 Precisa de Ajuda?

Se ainda tiver problemas:

1. Verifique se o `.env` está configurado corretamente
2. Certifique-se de que o bot está como admin no canal
3. Verifique as permissões do bot (opção 2 do script)
4. Tente deletar `my_account.session` e executar novamente

---

**✨ Tudo pronto! Execute o script e descubra os IDs dos seus canais.**
