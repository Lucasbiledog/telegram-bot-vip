# Guia de Integração - Sistema de Envio Automático

## 📋 Resumo das Mudanças

O bot agora usa um sistema simplificado de envio automático:
- **VIP:** 1 arquivo por dia às 15h (diariamente)
- **FREE:** 1 arquivo por semana às 15h (quartas-feiras)
- Arquivos vêm do grupo fonte: `-1003080645605`
- Sistema rastreia histórico para não repetir arquivos

## 🔧 Modificações Necessárias no main.py

### 1. Importar o novo módulo

Adicione no início do main.py (após outros imports):

```python
# Sistema de envio automático
from auto_sender import (
    SourceFile,
    SentFile,
    setup_auto_sender,
    index_message_file,
    send_daily_vip_file,
    send_weekly_free_file,
    get_stats,
    reset_sent_history,
    SOURCE_CHAT_ID
)
```

### 2. Configurar IDs dos Canais

No início do main.py, configure os IDs (já existem como variáveis, mas certifique-se que estão corretos):

```python
# IDs dos canais (destinos)
VIP_CHANNEL_ID = int(os.getenv("VIP_CHANNEL_ID", "-1002791988432"))  # Canal VIP
FREE_CHANNEL_ID = int(os.getenv("FREE_CHANNEL_ID", "-1002932075976"))  # Canal FREE
```

### 3. Adicionar Tabelas ao Banco de Dados

Adicione as classes `SourceFile` e `SentFile` ao Base do SQLAlchemy.

Na seção de modelos do banco, após as classes existentes:

```python
# Importar como Base do SQLAlchemy
from auto_sender import SourceFile, SentFile

# OU copiar as definições:
class SourceFile(Base):
    __tablename__ = "source_files"

    id = Column(Integer, primary_key=True)
    file_id = Column(String, nullable=False)
    file_unique_id = Column(String, nullable=False, unique=True, index=True)
    file_type = Column(String, nullable=False)
    message_id = Column(Integer, nullable=False, index=True)
    source_chat_id = Column(BigInteger, nullable=False)
    caption = Column(Text, nullable=True)
    file_name = Column(String, nullable=True)
    file_size = Column(BigInteger, nullable=True)
    indexed_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    active = Column(Boolean, default=True)

class SentFile(Base):
    __tablename__ = "sent_files"

    id = Column(Integer, primary_key=True)
    file_unique_id = Column(String, nullable=False, index=True)
    file_type = Column(String, nullable=False)
    message_id = Column(Integer, nullable=False)
    source_chat_id = Column(BigInteger, nullable=False)
    sent_to_tier = Column(String, nullable=False, index=True)
    sent_at = Column(DateTime(timezone=True), nullable=False)
    caption = Column(String, nullable=True)
```

### 4. Configurar o Sistema no Startup

No evento de startup do FastAPI ou após inicializar o bot:

```python
# Configurar sistema de envio automático
setup_auto_sender(VIP_CHANNEL_ID, FREE_CHANNEL_ID)
```

### 5. Adicionar Handler de Indexação

Registre o handler para indexar automaticamente arquivos do grupo fonte:

```python
async def auto_index_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler que indexa automaticamente arquivos do grupo fonte"""
    with SessionLocal() as session:
        await index_message_file(update, session)

# Registrar handler (aceita qualquer tipo de mídia)
application.add_handler(
    MessageHandler(
        filters.Chat(chat_id=SOURCE_CHAT_ID) &
        (filters.PHOTO | filters.VIDEO | filters.Document.ALL |
         filters.ANIMATION | filters.AUDIO),
        auto_index_handler
    ),
    group=10  # Grupo separado para não conflitar com outros handlers
)
```

### 6. Configurar Jobs Agendados

Use o APScheduler (já configurado no bot) ou o sistema de jobs do python-telegram-bot:

```python
import pytz

# Timezone
BR_TZ = pytz.timezone('America/Sao_Paulo')

# Job diário VIP (15h)
async def daily_vip_job(context: ContextTypes.DEFAULT_TYPE):
    """Job diário para envio VIP"""
    with SessionLocal() as session:
        await send_daily_vip_file(context.bot, session)

# Job semanal FREE (15h quartas)
async def weekly_free_job(context: ContextTypes.DEFAULT_TYPE):
    """Job semanal para envio FREE"""
    with SessionLocal() as session:
        await send_weekly_free_file(context.bot, session)

# Registrar jobs
job_queue = application.job_queue

# VIP: Diariamente às 15h
job_queue.run_daily(
    daily_vip_job,
    time=dt.time(hour=15, minute=0, second=0, tzinfo=BR_TZ),
    name='daily_vip_send'
)

# FREE: Diariamente às 15h (a função verifica se é quarta-feira)
job_queue.run_daily(
    weekly_free_job,
    time=dt.time(hour=15, minute=0, second=0, tzinfo=BR_TZ),
    name='weekly_free_send'
)
```

### 7. Adicionar Comandos de Administração

```python
async def stats_auto_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mostra estatísticas do sistema de envio automático"""
    if not is_admin(update.effective_user.id):
        return

    with SessionLocal() as session:
        stats = await get_stats(session)

    msg = (
        f"📊 <b>Estatísticas do Sistema de Envio Automático</b>\n\n"
        f"📁 Arquivos indexados: {stats['indexed_files']}\n\n"
        f"👑 <b>VIP:</b>\n"
        f"  • Enviados: {stats['vip']['total_sent']}\n"
        f"  • Disponíveis: {stats['vip']['available']}\n"
        f"  • Último envio: {stats['vip']['last_sent'].strftime('%d/%m/%Y %H:%M') if stats['vip']['last_sent'] else 'Nunca'}\n\n"
        f"🆓 <b>FREE:</b>\n"
        f"  • Enviados: {stats['free']['total_sent']}\n"
        f"  • Disponíveis: {stats['free']['available']}\n"
        f"  • Último envio: {stats['free']['last_sent'].strftime('%d/%m/%Y %H:%M') if stats['free']['last_sent'] else 'Nunca'}"
    )

    await update.effective_message.reply_text(msg, parse_mode='HTML')

async def reset_history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reseta histórico de envios"""
    if not is_admin(update.effective_user.id):
        return

    tier = context.args[0] if context.args else None

    if tier and tier not in ['vip', 'free']:
        await update.effective_message.reply_text("Uso: /reset_history [vip|free]")
        return

    with SessionLocal() as session:
        count = await reset_sent_history(session, tier)

    tier_name = tier.upper() if tier else "TODOS"
    await update.effective_message.reply_text(
        f"✅ Histórico resetado: {count} registros removidos ({tier_name})"
    )

async def test_send_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Testa envio manual (admin)"""
    if not is_admin(update.effective_user.id):
        return

    tier = context.args[0] if context.args else 'vip'

    if tier not in ['vip', 'free']:
        await update.effective_message.reply_text("Uso: /test_send [vip|free]")
        return

    await update.effective_message.reply_text(f"🔄 Testando envio {tier.upper()}...")

    with SessionLocal() as session:
        if tier == 'vip':
            await send_daily_vip_file(context.bot, session)
        else:
            await send_weekly_free_file(context.bot, session)

    await update.effective_message.reply_text(f"✅ Teste de envio {tier.upper()} concluído!")

# Registrar comandos
application.add_handler(CommandHandler("stats_auto", stats_auto_cmd))
application.add_handler(CommandHandler("reset_history", reset_history_cmd))
application.add_handler(CommandHandler("test_send", test_send_cmd))
```

## ⚙️ Variáveis de Ambiente

Adicione ao arquivo `.env`:

```env
# Canais de destino
VIP_CHANNEL_ID=-1002791988432
FREE_CHANNEL_ID=-1002932075976

# Grupo fonte já está definido no código como -1003080645605
```

## 🚀 Fluxo de Funcionamento

1. **Indexação Automática:**
   - Admin envia arquivos no grupo fonte (-1003080645605)
   - Bot indexa automaticamente na tabela `source_files`

2. **Envio Agendado:**
   - **15h diariamente:** Bot busca arquivo aleatório não enviado e envia no canal VIP
   - **15h quartas:** Bot busca arquivo aleatório não enviado e envia no canal FREE

3. **Controle de Repetição:**
   - Cada envio é registrado na tabela `sent_files`
   - Bot nunca envia o mesmo arquivo duas vezes para o mesmo tier

## 📊 Comandos Disponíveis

- `/stats_auto` - Ver estatísticas do sistema
- `/reset_history [vip|free]` - Resetar histórico (recomeçar envios)
- `/test_send [vip|free]` - Testar envio manual

## 🔄 Migração do Sistema Antigo

O sistema antigo de packs pode ser mantido ou removido, dependendo da necessidade.
As tabelas `packs` e `pack_files` não são mais usadas pelo novo sistema.

## ⚠️ Importante

1. O bot precisa ser **ADMIN** no grupo fonte para ler mensagens
2. O bot precisa ter permissão de **POSTAR** nos canais VIP e FREE
3. Envie alguns arquivos no grupo fonte para popular o índice inicial
4. Use `/test_send` para testar antes do horário agendado
