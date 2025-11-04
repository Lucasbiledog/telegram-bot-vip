# 🗄️ Configurar PostgreSQL no Render

## Passo a Passo Completo

### **1. Criar Banco de Dados**

1. Acesse: https://dashboard.render.com
2. Clique em **"New +"** → **"PostgreSQL"**
3. Preencha o formulário:

```
Name: telegram-bot-db
Database: telegram_bot
User: telegram_user (ou deixe automático)
Region: Oregon (US West) - mesma região do bot
PostgreSQL Version: 16 (mais recente)
Datadog API Key: (deixe vazio)
Instance Type: Free
```

4. Clique em **"Create Database"**
5. Aguarde 2-3 minutos enquanto o banco é provisionado

---

### **2. Copiar DATABASE_URL**

Após o banco ser criado:

1. Você será redirecionado para a página do banco
2. Procure por **"Connections"** ou **"Info"**
3. Você verá duas URLs:

**External Database URL** (para conexões externas):
```
postgresql://telegram_user:abc123...@dpg-xxxxx-a.oregon-postgres.render.com/telegram_bot
```

**Internal Database URL** (para serviços no Render):
```
postgresql://telegram_user:abc123...@dpg-xxxxx/telegram_bot
```

4. **COPIE a "Internal Database URL"** (é mais rápida para serviços no Render)

---

### **3. Configurar no Serviço do Bot**

1. No dashboard do Render, vá em **"Services"** ou volte para a lista de serviços
2. Clique no seu serviço do bot (provavelmente: `telegram-bot-vip`)
3. Vá em **"Environment"** (menu lateral esquerdo)
4. Procure a variável `DATABASE_URL`

**Se já existe:**
- Clique em **"Edit"**
- Cole a nova URL que você copiou
- Clique em **"Save Changes"**

**Se não existe:**
- Clique em **"Add Environment Variable"**
- **Key**: `DATABASE_URL`
- **Value**: Cole a URL que você copiou
- Clique em **"Save Changes"**

5. O bot vai **reiniciar automaticamente**

---

### **4. Verificar Conexão**

Aguarde 2-3 minutos para o bot reiniciar e inicializar o banco.

**Nos logs do Render, você deve ver:**

```
[DB] Tentativa 1/3 de conectar ao banco...
[DB] ✅ Conexão estabelecida com sucesso!
[SCHEMA] Inicializando schema (tentativa 1/3)...
[SCHEMA] ✅ Schema inicializado com sucesso!
✅ Bot inicializado com sucesso!
```

**Se ver isso, está TUDO CERTO! ✅**

---

### **5. Testar no Telegram**

Envie para o bot:

```
/stats_auto
```

**Resposta esperada:**
```
📊 Estatísticas do Sistema Auto-Send

📁 Arquivos indexados: 0
📤 VIP enviados: 0
📤 FREE enviados: 0
✅ VIP disponíveis: 0
✅ FREE disponíveis: 0
```

Se aparecer isso (mesmo com zeros), significa que o banco está funcionando!

---

## 🔍 Troubleshooting

### **Erro: "connection refused"**

**Causa:** Usando External URL ao invés de Internal URL

**Solução:**
- Certifique-se de usar a **Internal Database URL** (sem `-a` no host)
- Formato correto: `postgresql://user:pass@dpg-xxxxx/dbname`
- Formato errado: `postgresql://user:pass@dpg-xxxxx-a.oregon-postgres.render.com/dbname`

---

### **Erro: "authentication failed"**

**Causa:** Credenciais incorretas

**Solução:**
1. Volte na página do banco PostgreSQL
2. Copie novamente a URL completa (com senha)
3. Cole exatamente como está

---

### **Erro: "SSL connection closed"**

**Causa:** URL antiga ou banco desativado

**Solução:**
- Verifique se o banco está com status **"Available"** (verde)
- Se estiver "Suspended" ou "Expired", precisa criar um novo

---

## 📊 Limites do PostgreSQL Free

| Item | Limite |
|------|--------|
| **Storage** | 1 GB |
| **Bandwidth** | Ilimitado |
| **Connections** | 97 simultâneas |
| **Tempo** | Expira após 90 dias |
| **Backups** | Não inclusos (manual) |

**Dica:** Após 90 dias, você pode criar outro banco gratuito e migrar os dados.

---

## 🔐 Segurança

✅ **Boas práticas:**
- Nunca compartilhe a DATABASE_URL publicamente
- Não commite no GitHub (use .env local apenas para testes)
- Use Internal URL quando possível (mais seguro)

❌ **Evite:**
- Copiar External URL em lugares públicos
- Fazer hard-code da URL no código
- Compartilhar credenciais

---

## 💾 Backup Manual (Opcional)

Para fazer backup do banco antes que expire:

```bash
# Instalar pg_dump localmente
# Windows: https://www.postgresql.org/download/windows/

# Fazer backup
pg_dump "postgresql://user:pass@host/db" > backup.sql

# Restaurar em novo banco (quando criar)
psql "postgresql://user:pass@newhost/newdb" < backup.sql
```

---

## ✅ Checklist Final

Antes de prosseguir, certifique-se:

- [ ] Banco PostgreSQL criado no Render
- [ ] Status do banco: **Available** (verde)
- [ ] DATABASE_URL copiada (Internal)
- [ ] DATABASE_URL configurada no bot (Environment)
- [ ] Bot reiniciado automaticamente
- [ ] Logs mostram "✅ Conexão estabelecida"
- [ ] `/stats_auto` funcionando no Telegram

---

**Se todos os itens estiverem ✅, você está pronto!** 🎉

Próximo passo: Enviar arquivos no grupo fonte para testar o sistema de indexação automática.

---

Última atualização: 04/11/2025
