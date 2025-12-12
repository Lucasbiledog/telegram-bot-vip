# Guia: Criar Sessão Pyrogram para Scan Completo

O comando `/scanfull` usa o Pyrogram (User API) para ler TODO o histórico do grupo fonte. Em produção (Render), não há terminal interativo para digitar o código SMS, então precisamos criar a sessão localmente primeiro.

## Passo a Passo

### 1. Criar Sessão Localmente (no seu computador)

Execute o script de criação de sessão:

```bash
python criar_sessao_pyrogram.py
```

**O que vai acontecer:**
1. Script vai pedir seu **número de telefone** (formato internacional: +5511999999999)
2. Você receberá um **código SMS** no Telegram
3. Digite o código quando solicitado
4. Se sua conta tiver **verificação em 2 fatores**, digite a senha
5. Um arquivo `bot_session.session` será criado na pasta do projeto

**Exemplo de saída:**
```
============================================================
CRIANDO SESSAO DO PYROGRAM
============================================================

[*] API_ID: 21891661
[*] API_HASH: 3011acf0af...

[!] Você receberá um código SMS no seu Telegram
[!] Digite o código quando solicitado

[*] Iniciando autenticação...
Enter phone number: +5511999999999
Enter code: 12345
[OK] Sessão criada com sucesso!
[*] Usuário: Seu Nome (@seu_username)
[*] ID: 123456789

[OK] Arquivo de sessão criado: bot_session.session
```

### 2. Fazer Upload da Sessão para o Render

**Opção A: Via Git (Recomendado)**

⚠️ **ATENÇÃO:** O arquivo `.session` contém credenciais! Adicione ao `.gitignore` se for repositório público.

```bash
# Se for repositório PRIVADO, pode fazer commit normalmente:
git add bot_session.session
git commit -m "Add Pyrogram session"
git push
```

**Opção B: Via Render Disk (se usar persistent disk)**

1. Configure um persistent disk no Render
2. Faça upload do arquivo via SSH/SFTP

**Opção C: Recriar em Produção (avançado)**

Se quiser, pode criar a sessão diretamente no Render usando `render ssh` e executando o script lá, mas é mais complicado.

### 3. Verificar no Render

Após fazer upload:

1. Acesse o Render Dashboard
2. Vá em **Logs**
3. O arquivo `bot_session.session` deve estar na pasta do projeto
4. Teste o comando `/scanfull` no bot

## Troubleshooting

### Erro: "API_ID or API_HASH invalid"
- Verifique se `TELEGRAM_API_ID` e `TELEGRAM_API_HASH` estão corretos no `.env`
- Obtenha novos em: https://my.telegram.org/apps

### Erro: "PHONE_CODE_INVALID"
- Digite o código SMS exatamente como recebeu (apenas números, sem espaços)

### Erro: "SESSION_PASSWORD_NEEDED"
- Sua conta tem verificação em 2 fatores
- Digite sua senha do Telegram quando solicitado

### Erro: "bot_session.session not found" (no Render)
- O arquivo não foi feito upload
- Verifique se está na raiz do projeto
- Tente fazer commit e push novamente

### Arquivo .session sumiu após deploy
- Render deleta arquivos não comitados
- Use persistent disk OU
- Faça commit do arquivo .session (apenas em repos privados!)

## Segurança

⚠️ **IMPORTANTE:**

1. **NUNCA** compartilhe o arquivo `.session`
2. Ele contém credenciais de acesso à sua conta Telegram
3. Se seu repositório for **público**, adicione ao `.gitignore`:
   ```
   # .gitignore
   *.session
   *.session-journal
   ```
4. Use repositório **privado** se possível

## Como Funciona

- O Pyrogram salva a autenticação em um arquivo `.session`
- Este arquivo é binário e criptografado
- Uma vez criado, não precisa mais do código SMS
- O bot usará este arquivo automaticamente para autenticar

## Comandos do Bot

Após configurar a sessão:

- `/scanfull` - Escaneia TODAS as mensagens do grupo fonte
- `/scanfull 1000` - Escaneia últimas 1000 mensagens
- `/scanfull 50000` - Escaneia últimas 50000 mensagens

## Dúvidas?

Se algo der errado:
1. Verifique os logs do Render
2. Tente recriar a sessão localmente
3. Certifique-se que o arquivo foi enviado corretamente
