# 🗄️ Configuração do Supabase PostgreSQL

O banco de dados PostgreSQL do Render **expira após 30 dias no plano gratuito**. Use o **Supabase** que oferece **500 MB permanentes grátis**.

---

## ✅ PASSO 1: Criar Projeto no Supabase

1. **Acesse**: https://supabase.com/
2. **Crie uma conta** (ou faça login)
3. Clique em **"New Project"**
4. Preencha:
   - **Name**: `telegram-bot-vip`
   - **Database Password**: Crie uma senha forte (anote ela!)
   - **Region**: **South America (São Paulo)** (mais próximo do Brasil)
   - **Pricing Plan**: **Free** (500 MB permanente)
5. Clique em **"Create new project"**
6. Aguarde ~2 minutos para o projeto ser provisionado

---

## ✅ PASSO 2: Obter a Connection String (ATENÇÃO!)

### **MUITO IMPORTANTE: Use a porta 6543 (Pooler) e NÃO a 5432!**

1. No painel do Supabase, vá em **"Project Settings"** (engrenagem no canto inferior esquerdo)
2. Clique em **"Database"** no menu lateral
3. Role até a seção **"Connection string"**
4. **ATENÇÃO**: Selecione a aba **"Connection pooling"** (NÃO use "Direct connection")
5. Copie a URI que deve ter este formato:
   ```
   postgresql://postgres.xxxxxxxxxxxxx:[YOUR-PASSWORD]@aws-0-sa-east-1.pooler.supabase.com:6543/postgres
   ```

6. **CERTIFIQUE-SE** de que tem:
   - ✅ `.pooler.supabase.com` (com ".pooler")
   - ✅ Porta **6543** (NÃO 5432!)
   - ✅ Substitua `[YOUR-PASSWORD]` pela sua senha

7. **ADICIONE parâmetros de segurança** ao final:
   ```
   ?sslmode=require&connect_timeout=10
   ```

### **Exemplo final correto**:
```
postgresql://postgres.abcdefghijklmnop:MinHa$enh@F0rt3@aws-0-sa-east-1.pooler.supabase.com:6543/postgres?sslmode=require&connect_timeout=10
```

### **❌ ERRADO** (porta 5432 - vai dar erro!):
```
postgresql://postgres.xxx:senha@db.pghjvkgawvkyjhrlpjes.supabase.co:5432/postgres
```

---

## ✅ PASSO 3: Configurar no Render

### **Opção A: Via Dashboard do Render**

1. Acesse https://dashboard.render.com/
2. Clique no seu web service (telegram-bot-vip-hfn7)
3. Vá em **"Environment"** (menu lateral)
4. Procure `DATABASE_URL`:
   - Se existir, clique em **"Edit"**
   - Se não existir, clique em **"Add Environment Variable"**
5. Cole a connection string do Supabase (COM OS PARÂMETROS!):
   ```
   Key: DATABASE_URL
   Value: postgresql://postgres.xxxxx:[SUA-SENHA]@aws-0-sa-east-1.pooler.supabase.com:6543/postgres?sslmode=require&connect_timeout=10
   ```
6. Clique em **"Save Changes"**
7. No topo, clique em **"Manual Deploy"** → **"Deploy latest commit"**

### **Opção B: Via Arquivo .env (Local)**

Se você estiver rodando localmente, edite o arquivo `.env`:

```env
# Supabase PostgreSQL (Grátis permanente - 500 MB)
DATABASE_URL=postgresql://postgres.xxxxx:[SUA-SENHA]@aws-0-sa-east-1.pooler.supabase.com:6543/postgres
```

---

## ✅ PASSO 4: Verificar Conexão

Após configurar e fazer deploy, veja os logs no Render:

1. Vá em **"Logs"** (menu lateral)
2. Procure por:
   ```
   ✅ [DB] Conexão estabelecida com sucesso!
   ✅ [SCHEMA] Schema inicializado
   ```

Se ver isso, funcionou! 🎉

---

## ✅ PASSO 5: Indexar Arquivos

Agora que o banco está funcionando, faça o scan dos arquivos:

1. Abra o bot no Telegram
2. Envie o comando:
   ```
   /scan_full
   ```
3. Na primeira vez, você receberá um código SMS
4. Digite o código e aguarde
5. O bot indexará todos os arquivos do grupo fonte

---

## 📊 Verificar Stats

Depois do scan, envie:
```
/stats
```

Deve mostrar:
```
📊 VIP: 150 arquivos indexados
📊 FREE: 150 arquivos indexados
```

---

## 🔧 Solução de Problemas

### ❌ Erro: "Network is unreachable" ou "connection to server failed"

**Este é o erro MAIS COMUM!**

```
connection to server at "db.pghjvkgawvkyjhrlpjes.supabase.co" (2600:1f1e:...), port 5432 failed: Network is unreachable
```

**Causa**: Você copiou a connection string ERRADA (porta 5432 ao invés de 6543)

**Solução**:

1. **APAGUE** a connection string atual
2. Volte no Supabase → Project Settings → Database
3. **SELECIONE a aba "Connection pooling"** (NÃO "Direct connection"!)
4. Copie a URL que deve conter:
   - ✅ `.pooler.supabase.com` (com ".pooler")
   - ✅ Porta **6543**
5. Adicione os parâmetros no final:
   ```
   ?sslmode=require&connect_timeout=10
   ```

**URL CORRETA**:
```
postgresql://postgres.xxx:senha@aws-0-sa-east-1.pooler.supabase.com:6543/postgres?sslmode=require&connect_timeout=10
```

**URL ERRADA** (vai dar erro!):
```
postgresql://postgres.xxx:senha@db.pghjvkgawvkyjhrlpjes.supabase.co:5432/postgres
```

---

### ❌ Erro: "could not translate host name"

**Causa**: Connection string incorreta ou senha errada

**Solução**:
1. Volte no Supabase → Settings → Database
2. Copie a connection string da aba **"Connection pooling"**
3. Certifique-se de substituir `[YOUR-PASSWORD]` pela senha correta
4. Atualize no Render e redeploy

---

### ❌ Erro: "SSL connection required"

**Causa**: Falta parâmetro SSL na URL

**Solução**: Certifique-se de ter `?sslmode=require` no final da URL:
```
postgresql://postgres.xxx:senha@aws-0-sa-east-1.pooler.supabase.com:6543/postgres?sslmode=require&connect_timeout=10
```

---

### ❌ Erro: "too many connections"

**Causa**: Limite de conexões atingido ou usando porta direta

**Solução**:
1. Certifique-se de estar usando **porta 6543** (Connection Pooler)
2. Verifique se a URL contém `.pooler.supabase.com`
3. Se persistir, aumente o timeout: `?connect_timeout=30`

---

## 🎯 Vantagens do Supabase

✅ **500 MB grátis permanente** (vs Render que expira em 30 dias)
✅ **Backups automáticos diários** (7 dias de retenção)
✅ **Dashboard web** para visualizar tabelas
✅ **Região São Paulo** (baixa latência)
✅ **SSL incluído** por padrão
✅ **Connection pooling** integrado

---

## 📝 Próximos Passos

Após configurar o banco:

1. ✅ Fazer scan com `/scan_full`
2. ✅ Verificar stats com `/stats`
3. ✅ Testar envio com `/test_send_vip`
4. ✅ Aguardar até 15h para envio automático

---

**Precisa de ajuda?** Entre em contato com suporte técnico.
