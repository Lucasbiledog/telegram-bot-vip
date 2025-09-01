# Telegram Bot VIP

Este repositório contém um bot de Telegram para gerenciamento de planos VIP.

## Instalação

As dependências estão divididas em dois arquivos:

- `requirements.txt` – bibliotecas essenciais para execução do bot.
- `requirements-extras.txt` – integrações opcionais (Stripe, Google, Web3, Web3Auth etc.).

Instale apenas o núcleo:

```bash
pip install -r requirements.txt
```

Ou inclua também as dependências extras:

```bash
pip install -r requirements.txt -r requirements-extras.txt
```

## Atualização de dependências

Os arquivos `requirements*.txt` agora possuem versões fixadas. Para atualizar as
dependências e gerar um arquivo de *lock* com todas as versões transitivas,
instale o [pip-tools](https://github.com/jazzband/pip-tools) e execute:

```bash
pip-compile requirements.txt requirements-extras.txt
```

O comando acima produz `requirements.lock`. Reexecute `pip-compile` sempre que
for necessário subir versões e, em seguida, rode a suíte de testes.

## Redes suportadas pelo Blockchair

As seguintes redes EVM são utilizadas para verificar transações via API pública do Blockchair. Todas utilizam 18 casas decimais.

- Ethereum
- Binance Smart Chain
- Polygon
- Arbitrum
- Avalanche
- Fantom
- Base
- Optimism
- Gnosis
- Celo

## Integração com Etherscan

Transações na rede Ethereum também podem ser verificadas por meio da API do [Etherscan](https://etherscan.io/). Defina `ETHERSCAN_API_KEY` no ambiente para habilitar a verificação de pagamentos via Etherscan.
