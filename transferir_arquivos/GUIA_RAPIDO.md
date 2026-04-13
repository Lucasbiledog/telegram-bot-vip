# 🚀 Guia Rápido - Transferir Arquivos Telegram

## ✅ Instalação Completa!

As dependências já foram instaladas com sucesso:
- ✅ pyrogram
- ✅ tgcrypto
- ✅ python-dotenv
- ✅ pysocks

## 📝 Passo a Passo

### 1️⃣ Descobrir IDs dos Grupos

```bash
cd transferir_arquivos
python descobrir_ids.py
```

**No menu, escolha:**
- Opção 1: Lista TODOS os seus grupos com IDs
- Opção 2: Busca um grupo específico

**Na primeira vez**, vai pedir:
1. Seu número de telefone (com código do país, ex: +5511999999999)
2. Código de verificação (SMS)
3. Senha 2FA (se tiver)

Isso cria um arquivo `.session` que guarda o login.

### 2️⃣ Transferir Arquivos

```bash
python transferir_arquivos_user.py
```

**O script vai perguntar:**

1. **ID do grupo FONTE** (copie do passo 1)
   - Exemplo: `-1003080645605`

2. **ID do grupo DESTINO** (onde você é admin)
   - Exemplo: `-1002345678901`

3. **Filtro de tipo** (opcional):
   - `foto` - Só fotos
   - `video` - Só vídeos
   - `documento` - Só documentos
   - *Deixe em branco para TODOS*

4. **Limite de mensagens**:
   - Digite um número (ex: `100`)
   - *Deixe em branco para TODAS*

5. **Delay entre envios**:
   - Recomendado: `0.5` a `2` segundos
   - *Deixe em branco para 0.5*

### 3️⃣ Aguarde a Transferência

O script vai:
- ✅ Escanear o grupo fonte
- ✅ Mostrar quantos arquivos encontrou
- ✅ Pedir confirmação
- ✅ Transferir um por um
- ✅ Mostrar relatório final

## 💡 Dicas Importantes

### Performance
- **Teste primeiro**: Use limite de 10-20 arquivos para testar
- **Depois rode tudo**: Deixe limite em branco para transferir tudo
- **Delay adequado**: 0.5-1s para poucos arquivos, 1-2s para muitos

### Filtros Úteis
```
video     - Apenas vídeos (filmes, séries, etc)
documento - Apenas documentos (PDFs, ZIPs, etc)
foto      - Apenas fotos/imagens
audio     - Apenas músicas/áudios
```

### Permissões Necessárias
- ✅ **Grupo fonte**: Você deve ser membro (pode ser membro comum)
- ✅ **Grupo destino**: Você DEVE ser ADMIN (para enviar arquivos)

## ⚠️ Avisos

### FloodWait
Se aparecer "FloodWait", o script aguarda automaticamente. **Não interrompa!**

### ChatAdminRequired
Você não é admin no grupo destino. Peça permissões de administrador.

### ChannelPrivate
Você não tem acesso ao grupo. Verifique o ID correto.

## 📊 Exemplo Completo

```
$ python transferir_arquivos_user.py

ID do grupo FONTE: -1003080645605
ID do grupo DESTINO: -1002345678901
Filtro: video
Limite: 50
Delay: 1

> Escaneando grupo...
> Encontrados 120 vídeos
> Serão transferidos 50 vídeos
> Deseja continuar? s

[1/50] Transferindo video (msg 12345)...
        filme.mkv, 2.5GB
        ✅ Transferido com sucesso

...

📊 RELATÓRIO FINAL
📁 Arquivos encontrados: 50
✅ Transferidos: 48
❌ Erros: 2
📈 Taxa de sucesso: 96.0%
```

## 🔧 Resolução de Problemas

### "Não consigo ver meus grupos"
Execute: `python descobrir_ids.py` e escolha opção 1

### "Erro ao transferir"
Verifique se você é admin no grupo destino

### "Muito lento"
Aumente o delay (ex: 2 segundos) para evitar limites do Telegram

### "Session expired"
Delete o arquivo `.session` e rode novamente (vai pedir login)

## 🎯 Casos de Uso

### Backup completo de um grupo
```
Filtro: [vazio]
Limite: [vazio]
Delay: 1
```

### Só vídeos maiores que 1GB
```
Filtro: video
Limite: [vazio]
Delay: 2
```

### Testar antes de rodar tudo
```
Filtro: [vazio]
Limite: 10
Delay: 0.5
```

---

**Pronto para usar!** 🎉

Qualquer dúvida, consulte o `README.md` completo.
