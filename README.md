# Telegram Bot VIP

Este repositório contém um bot de Telegram para gerenciamento de planos VIP.

## Instalação

As dependências estão divididas em dois arquivos:

- `requirements-core.txt` – bibliotecas essenciais para execução do bot.
- `requirements-extras.txt` – integrações opcionais (Stripe, Google, Web3 etc.).

Instale apenas o núcleo:

```bash
pip install -r requirements-core.txt
```

Ou inclua também as dependências extras:

```bash
pip install -r requirements-core.txt -r requirements-extras.txt
```

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
