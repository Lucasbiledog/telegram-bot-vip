# 🚨 CORREÇÃO RÁPIDA: Erro "Network is unreachable"

## ❌ O QUE ESTÁ ACONTECENDO

Você está vendo este erro nos logs:
```
connection to server at "db.pghjvkgawvkyjhrlpjes.supabase.co" (2600:1f1e:...), port 5432 failed: Network is unreachable
```

**Problema**: Você copiou a connection string ERRADA do Supabase!

---

## ✅ SOLUÇÃO (5 minutos)

### **PASSO 1: Obter a URL CORRETA**

1. Acesse seu projeto no **Supabase**
2. Clique no **ícone de engrenagem** (canto inferior esquerdo) → **"Project Settings"**
3. No menu lateral, clique em **"Database"**
4. Role até **"Connection string"**
5. **IMPORTANTE**: Clique na aba **"Connection pooling"** (NÃO "Direct connection"!)

Você deve ver algo assim:
```
Session mode
URI: postgresql://postgres.xxxxxxxxx:[YOUR-PASSWORD]@aws-0-sa-east-1.pooler.supabase.com:6543/postgres
```

6. Clique em **"Copy"** para copiar
7. **Substitua** `[YOUR-PASSWORD]` pela senha que você criou
8. **ADICIONE** no final: `?sslmode=require&connect_timeout=10`

### **URL FINAL CORRETA**:
```
postgresql://postgres.xxxxxxxxx:SuaSenha123@aws-0-sa-east-1.pooler.supabase.com:6543/postgres?sslmode=require&connect_timeout=10
```

### **VERIFIQUE que tem**:
- ✅ `.pooler.supabase.com` (com ".pooler")
- ✅ Porta **6543** (NÃO 5432!)
- ✅ `?sslmode=require&connect_timeout=10` no final

---

### **PASSO 2: Atualizar no Render**

1. Acesse https://dashboard.render.com/
2. Clique no seu web service
3. Vá em **"Environment"** (menu lateral)
4. Encontre `DATABASE_URL` e clique em **"Edit"**
5. **APAGUE** a URL antiga
6. **COLE** a nova URL correta (com os parâmetros!)
7. Clique em **"Save Changes"**
8. No topo da página, clique em **"Manual Deploy"** → **"Deploy latest commit"**

---

### **PASSO 3: Verificar nos Logs**

Depois do deploy (leva ~3 minutos), vá em **"Logs"** e procure por:

✅ **SUCESSO**:
```
✅ [DB] Conexão estabelecida com sucesso!
✅ [SCHEMA] Schema inicializado
```

❌ **AINDA COM ERRO**:
```
Network is unreachable
```

Se ainda der erro, veja a seção "Checklist de Verificação" abaixo.

---

## 🔍 CHECKLIST DE VERIFICAÇÃO

Copie sua connection string e verifique:

- [ ] Contém `.pooler.supabase.com` (com ".pooler")
- [ ] Porta é **6543** (NÃO 5432)
- [ ] Senha está correta (sem `[YOUR-PASSWORD]`)
- [ ] Tem `?sslmode=require&connect_timeout=10` no final
- [ ] Não tem espaços em branco antes ou depois
- [ ] Não tem quebras de linha

---

## 📋 COMPARAÇÃO: CERTO vs ERRADO

### ✅ **URL CORRETA**:
```
postgresql://postgres.abc123:MinHa$enh@F0rt3@aws-0-sa-east-1.pooler.supabase.com:6543/postgres?sslmode=require&connect_timeout=10
```

**Características**:
- Tem `.pooler.supabase.com`
- Porta 6543
- Tem parâmetros SSL

---

### ❌ **URL ERRADA** (vai dar erro!):
```
postgresql://postgres.abc123:MinHa$enh@F0rt3@db.pghjvkgawvkyjhrlpjes.supabase.co:5432/postgres
```

**Problemas**:
- NÃO tem `.pooler`
- Porta 5432 (direta)
- Faltam parâmetros SSL

---

## 💡 ENTENDENDO A DIFERENÇA

| | Connection Pooling (CERTO) | Direct Connection (ERRADO) |
|---|---|---|
| **Porta** | 6543 ✅ | 5432 ❌ |
| **Host** | `.pooler.supabase.com` ✅ | `db.xxx.supabase.co` ❌ |
| **Funcionamento** | Pool de conexões otimizado | Conexão direta (limitada) |
| **Para Render** | ✅ Funciona sempre | ❌ Falha (IPv6/rede) |

---

## 🆘 AINDA NÃO FUNCIONOU?

### **Tente esta URL alternativa**:

Se mesmo com a porta 6543 não funcionar, tente adicionar mais parâmetros:

```
postgresql://postgres.xxx:senha@aws-0-sa-east-1.pooler.supabase.com:6543/postgres?sslmode=require&connect_timeout=10&keepalives=1&keepalives_idle=30
```

---

### **Verifique no Supabase**:

1. Vá em Project Settings → Database
2. Verifique se o status está **"Active"** (verde)
3. Se estiver **"Paused"**, clique em **"Resume"**

---

### **Teste a conexão localmente**:

Se você tem PostgreSQL instalado localmente, teste a conexão:

```bash
psql "postgresql://postgres.xxx:senha@aws-0-sa-east-1.pooler.supabase.com:6543/postgres?sslmode=require"
```

Se funcionar localmente mas não no Render, o problema é de configuração no Render.

---

## 📞 PRECISA DE MAIS AJUDA?

Se nada disso funcionar:

1. **Verifique o status do Supabase**: https://status.supabase.com/
2. **Recrie o projeto no Supabase** (às vezes resolve)
3. **Use outro serviço de banco** (Neon, Railway, etc.)

---

## ✅ DEPOIS DE CORRIGIR

Quando o banco estiver conectando, faça:

1. `/scan_full` - Indexar arquivos
2. `/stats` - Verificar arquivos indexados
3. `/test_send_vip` - Testar envio manual

**Os jobs automáticos rodam às 15h horário de Brasília!**
