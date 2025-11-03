# 🛠️ Scripts Utilitários do Bot

Este documento explica como usar os scripts auxiliares para configurar e gerenciar o bot.

---

## 📋 Scripts Disponíveis

### 1. `listar_grupos.py` - Listar Grupos e Canais
### 2. `scan_historico.py` - Indexar Histórico de Arquivos

---

## 🔍 1. LISTAR GRUPOS E CANAIS

**Arquivo:** `listar_grupos.py`

### O que faz:
- Lista todos os grupos/canais que o bot está
- Mostra IDs corretos para configuração
- Permite buscar por username (@nome)

### Como usar:

#### Windows:
```bash
cd C:\Users\Lucas Moura\Downloads\telegram_bot\bot-oficial
python listar_grupos.py
```

#### Linux/Mac:
```bash
cd /caminho/para/bot-oficial
python3 listar_grupos.py
```

### Menu do Script:

```
🤖 BOT TELEGRAM - LISTAR GRUPOS E CANAIS

Escolha uma opção:
1 - Listar todos os grupos/canais conhecidos
2 - Buscar grupo/canal por username (@nome)
3 - Sair
```

### Exemplo de Saída (Opção 1):

```
🔍 BUSCANDO GRUPOS E CANAIS DO BOT
================================================================

📋 Verificando IDs conhecidos:

📢 Canal
  📝 Título: UNREAL PACK VIP
  🆔 ID: -1002791988432
  🔗 Username: @unrealpackvip

👥 Supergrupo
  📝 Título: UNREAL PACK FREE
  🆔 ID: -1002932075976

👥 Grupo
  📝 Título: Arquivos Admin
  🆔 ID: -1003080645605

...
```

### Exemplo de Saída (Opção 2):

```
🔍 BUSCAR CANAL/GRUPO POR USERNAME
================================================================

Digite o username (com ou sem @): unrealpackvip

🔄 Buscando @unrealpackvip...

✅ Encontrado!

📢 Canal
  📝 Título: UNREAL PACK VIP
  🆔 ID: -1002791988432
  🔗 Username: @unrealpackvip
  📄 Descrição: Canal VIP com conteúdo exclusivo...

💡 Copie este ID: -1002791988432
```

### ✅ Quando Usar:

- ✅ Quando você não tem certeza dos IDs corretos
- ✅ Para verificar se bot está nos canais certos
- ✅ Antes de configurar o .env
- ✅ Para buscar um canal específico pelo @username

---

## 🔄 2. SCAN DE HISTÓRICO

**Arquivo:** `scan_historico.py`

### O que faz:
- Faz scan completo do grupo fonte
- Indexa TODOS os arquivos do histórico
- Lista arquivos já indexados
- Necessário rodar UMA VEZ após o deploy

### Como usar:

#### Windows:
```bash
cd C:\Users\Lucas Moura\Downloads\telegram_bot\bot-oficial
python scan_historico.py
```

#### Linux/Mac:
```bash
cd /caminho/para/bot-oficial
python3 scan_historico.py
```

### Menu do Script:

```
🤖 BOT TELEGRAM - SCAN DE HISTÓRICO

Escolha uma opção:
1 - Fazer scan do histórico (indexar arquivos)
2 - Listar arquivos já indexados
3 - Sair
```

### Opção 1 - Fazer Scan:

```
Limite de mensagens (0 = sem limite): 1000

🔍 SCAN DO HISTÓRICO DO GRUPO FONTE
======================================================================

📋 Grupo: Arquivos Admin
🆔 ID: -1003080645605

⏳ Iniciando scan... (isso pode demorar alguns minutos)

🔄 Método 1: Tentando buscar mensagens recentes...

✅ Indexado: video - 12345 (245.3 MB)
✅ Indexado: photo - 12346 (1.2 MB)
✅ Indexado: document - 12347 (89.5 MB)
⏭️  Já indexado: video - 12348
...

📊 RELATÓRIO DO SCAN
======================================================================

📨 Mensagens processadas: 150
✅ Arquivos indexados: 45
⏭️  Duplicados (já existiam): 105
❌ Erros: 0

📁 Tipos de arquivo encontrados:
   • video: 20
   • photo: 10
   • document: 15

💾 Total no banco (grupo -1003080645605): 150 arquivos

✅ Scan concluído com sucesso!

💡 PRÓXIMOS PASSOS:
1. Use /stats_auto no bot para ver estatísticas
2. Use /test_send vip para testar envio
3. O bot agora indexará novos arquivos automaticamente
```

### Opção 2 - Listar Indexados:

```
📋 ARQUIVOS INDEXADOS NO BANCO
======================================================================

📁 Total: 150 arquivos (mostrando últimos 50)

🆔 ID: 1
   Tipo: video
   Tamanho: 245.3 MB
   Caption: Meu Video.mp4
   Message ID: 12345
   Indexado: 03/11/2025 19:00

🆔 ID: 2
   Tipo: photo
   Tamanho: 1.2 MB
   Caption: Foto teste
   Message ID: 12346
   Indexado: 03/11/2025 19:01

...
```

### ⚠️ Limitações do Scan:

O script usa a **Bot API do Telegram**, que tem limitações:

- ❌ Não pode acessar histórico completo ilimitado
- ✅ Consegue acessar últimas ~100-1000 mensagens recentes
- ✅ Depois do scan inicial, indexa automaticamente novos arquivos

### 💡 Para Scan Completo Ilimitado:

Se precisar indexar TODO o histórico (milhares de mensagens antigas), use:

1. **Pyrogram** ou **Telethon** (User Bot)
2. Script separado com essas bibliotecas
3. Ou adicione arquivos manualmente ao grupo fonte

---

## 🚀 WORKFLOW RECOMENDADO

### Primeira vez (Configuração Inicial):

1. **Listar Canais:**
   ```bash
   python listar_grupos.py
   ```
   - Escolha opção 1
   - Copie os IDs corretos
   - Atualize o .env ou main.py

2. **Fazer Scan Inicial:**
   ```bash
   python scan_historico.py
   ```
   - Escolha opção 1
   - Digite limite: 1000 (ou 0 para sem limite)
   - Aguarde conclusão

3. **Verificar Indexação:**
   ```bash
   python scan_historico.py
   ```
   - Escolha opção 2
   - Confira se arquivos foram indexados

4. **Iniciar Bot:**
   ```bash
   python main.py
   ```
   - Bot já terá arquivos prontos para enviar
   - Use `/stats_auto` para confirmar

---

## 📝 EXEMPLOS DE USO

### Exemplo 1: Descobrir ID do Canal VIP

```bash
# Executar script
python listar_grupos.py

# Selecionar opção 2
Opção: 2

# Digitar username
Digite o username (com ou sem @): unrealpackvip

# Copiar ID da saída
💡 Copie este ID: -1002791988432

# Atualizar no .env
VIP_CHANNEL_ID=-1002791988432
```

### Exemplo 2: Indexar Arquivos Após Deploy

```bash
# Executar scan
python scan_historico.py

# Selecionar opção 1
Opção: 1

# Definir limite
Limite de mensagens (0 = sem limite): 0

# Aguardar conclusão
✅ Scan concluído com sucesso!

# Verificar no bot
/stats_auto
```

### Exemplo 3: Verificar Arquivos Indexados

```bash
# Executar scan
python scan_historico.py

# Selecionar opção 2
Opção: 2

# Ver lista de arquivos
📁 Total: 150 arquivos (mostrando últimos 50)
```

---

## ❓ PERGUNTAS FREQUENTES

### P: O scan não encontra nenhum arquivo?
**R:**
- Verifique se bot está no grupo fonte
- Confirme que bot tem permissão de leitura
- O Bot API tem limitações de histórico
- Tente enviar alguns arquivos novos primeiro

### P: Como indexar arquivos muito antigos?
**R:**
- Use Pyrogram ou Telethon para scan completo
- Ou envie arquivos novamente no grupo fonte
- Bot indexará automaticamente novos envios

### P: Preciso rodar scan toda vez?
**R:**
- ❌ NÃO! Apenas UMA VEZ após deploy inicial
- Depois, bot indexa automaticamente 24/7
- Use scan apenas para re-indexar se necessário

### P: Scan trava ou demora muito?
**R:**
- Use limite menor (ex: 100 mensagens)
- Grupos muito grandes podem demorar
- Bot API tem rate limits

### P: Como saber se scan funcionou?
**R:**
```bash
# No script, opção 2
python scan_historico.py
Opção: 2

# Ou no bot
/stats_auto
```

---

## ⚙️ REQUISITOS

### Python:
- Python 3.7 ou superior
- Bibliotecas do requirements.txt instaladas

### Variáveis de Ambiente:
```env
BOT_TOKEN=seu_token_aqui
# ou
TELEGRAM_BOT_TOKEN=seu_token_aqui
```

### Permissões do Bot:
- Bot deve estar nos grupos/canais
- Precisa permissão de LEITURA no grupo fonte
- Precisa permissão de ADMIN nos canais de destino

---

## 🐛 SOLUÇÃO DE PROBLEMAS

### Erro: "BOT_TOKEN não encontrado"
```bash
# Criar/editar .env
echo "BOT_TOKEN=seu_token_aqui" > .env
```

### Erro: "Erro ao acessar grupo"
```bash
# Verificar se bot está no grupo
python listar_grupos.py
```

### Erro: "Tabelas não criadas"
```bash
# Rodar migrate ou iniciar bot primeiro
python main.py
# Depois rodar scan
python scan_historico.py
```

---

## 📚 DOCUMENTAÇÃO RELACIONADA

- `GUIA_COMPLETO_FINAL.md` - Guia completo do sistema
- `MUDANCAS_IMPLEMENTADAS.md` - Changelog das alterações
- `INTEGRACAO_AUTO_SENDER.md` - Integração técnica

---

**Dúvidas?** Entre em contato com o suporte!
