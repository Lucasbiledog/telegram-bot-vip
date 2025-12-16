# 📥 Indexador de Arquivos do Telegram

Script simples para ler todos os arquivos do grupo no Telegram e colocá-los na fila de envio automático.

## 🎯 O que ele faz?

1. **Lê todo o histórico** do grupo fonte no Telegram
2. **Indexa todos os arquivos** (vídeos, documentos, fotos, áudios, etc)
3. **Coloca na fila** para envio automático programado:
   - **VIP**: 1 arquivo por dia às 15h
   - **FREE**: 1 arquivo por semana (quartas-feiras às 15h)

## ⚙️ Configuração

### 1. Obter credenciais da API do Telegram

1. Acesse: https://my.telegram.org/apps
2. Faça login com seu número de telefone
3. Clique em "API Development Tools"
4. Crie um novo aplicativo (se não tiver)
5. Copie o `api_id` e `api_hash`

### 2. Configurar variáveis de ambiente

Edite o arquivo `.env` e adicione:

```env
# API do Telegram (para indexação)
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=abc123def456...

# ID do grupo fonte (onde estão os arquivos)
SOURCE_CHAT_ID=-1003080645605

# Banco de dados (já configurado)
DATABASE_URL=postgresql://...
```

### 3. Instalar Pyrogram (se necessário)

```bash
pip install pyrogram tgcrypto
```

## 🚀 Como usar

Execute o script:

```bash
python ler_e_indexar_grupo.py
```

### Na primeira vez:

1. O script pedirá seu **número de telefone**
2. Você receberá um **código SMS**
3. Digite o código
4. Uma sessão será criada (`indexador_session.session`)
5. Nas próximas execuções, não precisará fazer login novamente

## 📊 O que acontece depois?

Depois de indexar os arquivos, o bot **enviará automaticamente**:

### Canal VIP
- **Frequência**: TODO DIA às 15h
- **Conteúdo**: 1 arquivo aleatório ainda não enviado
- **Tipos**: Todos (vídeos, documentos, fotos, etc)
- **Tamanho**: Sem limite

### Canal FREE
- **Frequência**: TODA QUARTA-FEIRA às 15h
- **Conteúdo**: 1 arquivo aleatório ainda não enviado
- **Tipos**: Todos exceto fotos
- **Tamanho**: Máximo 500MB
- **Filtro**: Sem arquivos divididos em partes (part1, part2, etc)

## 📌 Comandos úteis do bot

Depois de indexar, use no bot:

- `/stats_auto` - Ver estatísticas (arquivos indexados, enviados, disponíveis)
- `/test_send vip` - Testar envio manual VIP
- `/test_send free` - Testar envio manual FREE
- `/reset_history vip` - Resetar histórico VIP (recomeçar do zero)
- `/reset_history free` - Resetar histórico FREE

## 🔄 Como adicionar mais arquivos?

1. **Opção 1**: Poste novos arquivos no grupo fonte
   - O bot indexará automaticamente quando você rodar o script novamente

2. **Opção 2**: Use o comando `/scan_full` no bot
   - Escaneia apenas arquivos novos (mais rápido)

## 🛠️ Solução de problemas

### "TELEGRAM_API_ID não encontrado"
Configure as variáveis de ambiente no arquivo `.env`

### "Erro ao acessar grupo"
Certifique-se de que:
1. O ID do grupo está correto
2. Você é membro do grupo
3. O grupo não é privado/secreto

### "Módulo pyrogram não encontrado"
Instale: `pip install pyrogram tgcrypto`

## 💡 Dicas

- **Execute periodicamente** para adicionar novos arquivos
- **Não precisa parar o bot** para rodar o indexador
- **Arquivos duplicados** são detectados automaticamente
- **Grande quantidade de arquivos** pode demorar (seja paciente!)

## 🎯 Fluxo completo

```
┌─────────────────────────────────────────┐
│  1. Postar arquivos no grupo fonte      │
└──────────────┬──────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────┐
│  2. Rodar: python ler_e_indexar_grupo.py│
│     → Lê histórico completo              │
│     → Indexa no banco de dados           │
└──────────────┬──────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────┐
│  3. Bot envia automaticamente:          │
│     → VIP: Todo dia às 15h               │
│     → FREE: Quartas às 15h               │
└─────────────────────────────────────────┘
```

## ❓ Perguntas frequentes

**P: Posso mudar os horários de envio?**
R: Sim! Edite as funções `send_daily_vip_file` e `send_weekly_free_file` no arquivo `auto_sender.py`

**P: Como resetar tudo e recomeçar?**
R: Use `/reset_history all` no bot para apagar o histórico de envios

**P: Os arquivos são copiados ou movidos?**
R: Apenas **referenciados**. O bot copia do grupo fonte para os canais destino.

**P: Quantos arquivos posso indexar?**
R: Ilimitado! O banco de dados guarda apenas referências (file_id).

---

Feito com ❤️ para facilitar o gerenciamento de canais VIP/FREE
