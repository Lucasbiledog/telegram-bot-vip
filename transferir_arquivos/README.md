# 📤 Transferir Arquivos Entre Grupos Telegram

Scripts para transferir arquivos entre grupos do Telegram usando sua conta de usuário.

## 📋 Conteúdo

- `transferir_arquivos_user.py` - Script principal para transferir arquivos
- `descobrir_ids.py` - Descobrir IDs de grupos e canais
- `requirements.txt` - Dependências Python

## 🚀 Instalação

### 1. Instalar dependências:

```bash
pip install -r requirements.txt
```

Ou manualmente:

```bash
pip install pyrogram tgcrypto python-dotenv
```

### 2. Configurar variáveis de ambiente:

Certifique-se de que o arquivo `.env` na pasta raiz do projeto tem:

```env
TELEGRAM_API_ID=seu_api_id
TELEGRAM_API_HASH=seu_api_hash
```

**Como obter:**
1. Acesse https://my.telegram.org/apps
2. Faça login com seu número de telefone
3. Crie um novo app
4. Copie o `api_id` e `api_hash`

## 📖 Como Usar

### Passo 1: Descobrir IDs dos grupos

```bash
cd transferir_arquivos
python descobrir_ids.py
```

Este script mostra:
- Todos os grupos que você participa
- IDs de cada grupo
- Número de membros
- Username (se houver)

### Passo 2: Transferir arquivos

```bash
python transferir_arquivos_user.py
```

**O script vai perguntar:**

1. **ID do grupo fonte** - Onde estão os arquivos
2. **ID do grupo destino** - Para onde transferir (você deve ser admin)
3. **Filtro de tipo** (opcional):
   - `foto` - Apenas fotos
   - `video` - Apenas vídeos
   - `documento` - Apenas documentos
   - `audio` - Apenas áudios
   - `animacao` - GIFs/animações
   - `voice` - Mensagens de voz
   - `sticker` - Stickers
   - *Deixe em branco para todos os tipos*
4. **Limite de mensagens** - Quantas processar (branco = todas)
5. **Delay entre transferências** - Segundos de espera (recomendado: 0.5 a 2)

### Primeiro Uso

Na primeira execução, o Pyrogram vai pedir:
1. Seu número de telefone
2. Código de verificação (SMS)
3. Senha 2FA (se configurada)

Isso cria um arquivo de sessão que é reutilizado nas próximas execuções.

## ✨ Recursos

### transferir_arquivos_user.py

- ✅ Acesso total ao histórico do grupo
- ✅ Suporta todos os tipos de mídia
- ✅ Filtros por tipo de arquivo
- ✅ Proteção contra flood (rate limit)
- ✅ Preserva legendas e formatação
- ✅ Relatório detalhado ao final
- ✅ Estatísticas por tipo de arquivo

### descobrir_ids.py

- ✅ Lista todos os seus chats
- ✅ Mostra IDs de grupos, canais e conversas
- ✅ Busca chat específico por username ou ID
- ✅ Informações detalhadas (membros, descrição, etc)

## ⚠️ Requisitos

### Para o grupo FONTE:
- Você deve ser membro do grupo
- Acesso para ler mensagens

### Para o grupo DESTINO:
- Você deve ser **administrador** ou **criador**
- Permissão para enviar mensagens e mídia

## 🔐 Segurança

- Arquivos de sessão (`.session`) são criados localmente
- **NUNCA** compartilhe seus arquivos `.session`
- Mantenha seu `.env` privado
- Os scripts usam SUA conta, não um bot

## 💡 Dicas

### Performance
- Use delay de 0.5-1s para grupos pequenos
- Use delay de 1-2s para muitos arquivos
- O Telegram limita a taxa de envio (flood protection)

### Filtros
- Use filtros para transferir apenas tipos específicos
- Exemplo: apenas vídeos de um grupo de filmes

### Limites
- Teste primeiro com limite pequeno (ex: 50 mensagens)
- Depois rode sem limite para transferir tudo

## 🐛 Troubleshooting

### Erro: "FloodWait"
O Telegram está limitando suas requisições. O script aguarda automaticamente.

### Erro: "ChatAdminRequired"
Você não é admin no grupo destino. Peça permissões de admin.

### Erro: "ChannelPrivate"
Você não tem acesso ao grupo. Verifique se está no grupo correto.

### Erro: "api_id/api_hash invalid"
Verifique se copiou corretamente do https://my.telegram.org/apps

## 📊 Exemplos

### Transferir últimas 100 mensagens:
```
ID do grupo FONTE: -1003080645605
ID do grupo DESTINO: -1002345678901
Filtro: [deixe em branco]
Limite: 100
Delay: 0.5
```

### Transferir apenas vídeos:
```
ID do grupo FONTE: -1003080645605
ID do grupo DESTINO: -1002345678901
Filtro: video
Limite: [deixe em branco para todos]
Delay: 1
```

### Transferir tudo sem limite:
```
ID do grupo FONTE: -1003080645605
ID do grupo DESTINO: -1002345678901
Filtro: [deixe em branco]
Limite: [deixe em branco]
Delay: 1
```

## 📞 Suporte

Para dúvidas ou problemas:
1. Verifique se todas as dependências estão instaladas
2. Confirme que o `.env` está configurado corretamente
3. Teste primeiro com poucos arquivos (limite: 10)
4. Verifique as permissões nos grupos

## ⚖️ Aviso Legal

Use estes scripts de forma responsável:
- Respeite os termos de serviço do Telegram
- Não faça spam
- Tenha permissão para transferir os arquivos
- Respeite direitos autorais

---

**Criado para facilitar backup e organização de grupos Telegram** 🚀
