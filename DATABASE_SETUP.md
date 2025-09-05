# 🗃️ Configuração do Banco de Dados

## ⚠️ IMPORTANTE: Persistência de Dados

Para garantir que os **packs cadastrados não sejam perdidos** durante deployments, você DEVE configurar um banco PostgreSQL.

## 🚨 Problemas Atuais

- **SQLite em `/tmp/`**: Dados perdidos a cada redeploy
- **Banco em memória**: Dados perdidos a cada restart
- **Único que persiste**: PostgreSQL configurado corretamente

## 🔧 Como Configurar no Render.com

### 1. Adicionar Banco no `render.yaml`:
```yaml
services:
  - type: web
    name: telegram-bot
    env: python
    plan: free
    buildCommand: pip install -r requirements.txt
    startCommand: uvicorn main:app --host=0.0.0.0 --port=$PORT
    pythonVersion: 3.10
    autoDeploy: true

databases:
  - name: telegram-bot-db
    databaseName: telegram_bot
    user: telegram_user
    plan: free
```

### 2. O Render irá:
- Criar o banco PostgreSQL automaticamente
- Fornecer a `DATABASE_URL` como variável de ambiente
- Conectar automaticamente ao seu serviço

### 3. Verificar Configuração:
Quando o bot iniciar, você verá uma das mensagens:

✅ **CORRETO (PostgreSQL)**:
```
✅ Database schema initialized successfully (PostgreSQL)
✅ ✨ Data persistence ENABLED - safe for production!
```

⚠️ **TEMPORÁRIO (SQLite)**:
```
🟡 Database schema initialized successfully (TEMPORARY SQLite)
🟡 ⚠️ WARNING: Data will be lost on redeploy!
```

❌ **PERIGOSO (In-Memory)**:
```
🔶 Database schema initialized successfully (IN-MEMORY)
🔶 ⚠️ WARNING: All data will be lost on restart!
```

## 📋 Checklist de Deploy

Antes do deploy, verifique:

- [ ] `render.yaml` contém a seção `databases`
- [ ] PostgreSQL está configurado no painel do Render
- [ ] `DATABASE_URL` está sendo fornecida automaticamente
- [ ] Logs mostram "✅ PostgreSQL" na inicialização
- [ ] Testes de packs funcionam após redeploy

## 🆘 Resolução de Problemas

### Se ainda usar SQLite temporário:
1. Verifique se o banco PostgreSQL foi criado no painel do Render
2. Confirme que a `DATABASE_URL` está sendo passada para o serviço
3. Redeploy a aplicação
4. Verifique os logs de inicialização

### Migração de dados existentes:
Se você já tem packs importantes no SQLite temporário:
1. Configure PostgreSQL primeiro
2. Use ferramentas de migração ou recadastramento
3. SQLite em `/tmp/` será perdido no próximo deploy anyway

## ⚡ Desenvolvimento Local

Para desenvolvimento local, o bot usa:
1. `./bot.db` (arquivo local) - recomendado
2. PostgreSQL se `DATABASE_URL` estiver configurada
3. Fallback para SQLite em outros locais

---

**✨ Com PostgreSQL configurado, seus packs estarão SEGUROS contra deployments!**