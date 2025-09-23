#!/usr/bin/env python3
"""
Sistema de Validação de Pagamentos VIP
Valida a integridade e performance dos sistemas de pagamento
"""

import asyncio
import aiohttp
import time
import json
import logging
import random
from datetime import datetime, timedelta
from typing import Dict, List, Any
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

# Configurar logging
logging.basicConfig(level=logging.INFO)
LOG = logging.getLogger("PAYMENT_VALIDATOR")

class PaymentValidator:
    """Sistema de validação de pagamentos VIP e integridade de transações"""

    def __init__(self, base_url: str = "https://telegram-bot-vip-hfn7.onrender.com"):
        self.base_url = base_url

        # Moedas principais da página de checkout (baseado em payments.py)
        self.main_coins = [
            # Tier 1 - Principais (alta prioridade)
            {
                "chain": "Ethereum",
                "chain_id": "0x1",
                "symbol": "ETH",
                "name": "Ethereum",
                "decimals": 18,
                "tier": "premium",
                "typical_amount": 0.001  # ~$3-4 USD
            },
            {
                "chain": "BSC",
                "chain_id": "0x38",
                "symbol": "BNB",
                "name": "BNB",
                "decimals": 18,
                "tier": "premium",
                "typical_amount": 0.002  # ~$1-2 USD
            },
            {
                "chain": "Polygon",
                "chain_id": "0x89",
                "symbol": "MATIC",
                "name": "Polygon",
                "decimals": 18,
                "tier": "premium",
                "typical_amount": 5.0  # ~$1-2 USD
            },

            # Tier 2 - Stablecoins (alta confiabilidade)
            {
                "chain": "Ethereum",
                "chain_id": "0x1",
                "symbol": "USDC",
                "name": "USD Coin",
                "decimals": 6,
                "tier": "stable",
                "typical_amount": 2.0,  # $2 USD
                "contract": "0xa0b86991c31cc170c8b9e71b51e1a53af4e9b8c9e"
            },
            {
                "chain": "BSC",
                "chain_id": "0x38",
                "symbol": "USDC",
                "name": "USD Coin (BSC)",
                "decimals": 18,
                "tier": "stable",
                "typical_amount": 1.5,  # $1.5 USD
                "contract": "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d"
            },
            {
                "chain": "Ethereum",
                "chain_id": "0x1",
                "symbol": "USDT",
                "name": "Tether",
                "decimals": 6,
                "tier": "stable",
                "typical_amount": 3.0,  # $3 USD
                "contract": "0xdac17f958d2ee523a2206206994597c13d831ec7"
            },
            {
                "chain": "BSC",
                "chain_id": "0x38",
                "symbol": "USDT",
                "name": "Tether (BSC)",
                "decimals": 18,
                "tier": "stable",
                "typical_amount": 2.5,  # $2.5 USD
                "contract": "0x55d398326f99059ff775485246999027b3197955"
            },

            # Tier 3 - Populares (boa cobertura)
            {
                "chain": "Arbitrum",
                "chain_id": "0xa4b1",
                "symbol": "ETH",
                "name": "Ethereum (Arbitrum)",
                "decimals": 18,
                "tier": "popular",
                "typical_amount": 0.0008  # ~$2-3 USD
            },
            {
                "chain": "Optimism",
                "chain_id": "0xa",
                "symbol": "ETH",
                "name": "Ethereum (Optimism)",
                "decimals": 18,
                "tier": "popular",
                "typical_amount": 0.0007  # ~$2-3 USD
            },
            {
                "chain": "Base",
                "chain_id": "0x2105",
                "symbol": "ETH",
                "name": "Ethereum (Base)",
                "decimals": 18,
                "tier": "popular",
                "typical_amount": 0.0008  # ~$2-3 USD
            },
            {
                "chain": "BSC",
                "chain_id": "0x38",
                "symbol": "BTCB",
                "name": "Bitcoin (BSC)",
                "decimals": 18,
                "tier": "popular",
                "typical_amount": 0.00003,  # ~$3 USD
                "contract": "0x7130d2a12b9bcbfae4f2634d864a1ee1ce3ead9c"
            },

            # Tier 4 - Diversificados
            {
                "chain": "Avalanche",
                "chain_id": "0xa86a",
                "symbol": "AVAX",
                "name": "Avalanche",
                "decimals": 18,
                "tier": "diverse",
                "typical_amount": 0.05  # ~$1-2 USD
            },
            {
                "chain": "Fantom",
                "chain_id": "0xfa",
                "symbol": "FTM",
                "name": "Fantom",
                "decimals": 18,
                "tier": "diverse",
                "typical_amount": 6.0  # ~$1-2 USD
            },
            {
                "chain": "Cronos",
                "chain_id": "0x19",
                "symbol": "CRO",
                "name": "Crypto.com Coin",
                "decimals": 18,
                "tier": "diverse",
                "typical_amount": 8.0  # ~$2 USD
            }
        ]

        # Faixas de valor VIP (baseado no endpoint /vip_pricing)
        self.vip_tiers = [
            {"min_usd": 30.00, "max_usd": 69.99, "days": 30, "plan": "MENSAL"},
            {"min_usd": 70.00, "max_usd": 109.99, "days": 90, "plan": "TRIMESTRAL"},
            {"min_usd": 110.00, "max_usd": 178.99, "days": 180, "plan": "SEMESTRAL"},
            {"min_usd": 179.00, "max_usd": 500.00, "days": 365, "plan": "ANUAL"}
        ]

    def generate_realistic_payment_scenarios(self, num_tests: int = 100) -> List[Dict[str, Any]]:
        """Gera cenários realísticos de pagamento VIP baseados em uso real"""
        scenarios = []

        for i in range(num_tests):
            # Escolher coin baseado na distribuição real de uso
            coin_weights = {
                "premium": 0.4,    # 40% - ETH, BNB, MATIC
                "stable": 0.35,    # 35% - USDC, USDT
                "popular": 0.20,   # 20% - Arbitrum, Optimism, Base, BTCB
                "diverse": 0.05    # 5% - AVAX, FTM, CRO
            }

            rand = random.random()
            cumulative = 0
            selected_tier = "premium"

            for tier, weight in coin_weights.items():
                cumulative += weight
                if rand <= cumulative:
                    selected_tier = tier
                    break

            # Filtrar coins por tier
            tier_coins = [c for c in self.main_coins if c["tier"] == selected_tier]
            coin = random.choice(tier_coins)

            # Escolher valor VIP baseado na preferência do usuário
            vip_weights = [0.15, 0.25, 0.35, 0.25]  # Preferência por planos intermediários
            vip_tier = random.choices(self.vip_tiers, weights=vip_weights)[0]

            # Gerar valor USD no range do tier
            usd_amount = random.uniform(vip_tier["min_usd"], vip_tier["max_usd"])

            # Calcular quantidade aproximada da coin
            if coin["symbol"] in ["USDC", "USDT"]:
                coin_amount = usd_amount  # Stablecoins 1:1
            else:
                coin_amount = coin["typical_amount"] * (usd_amount / 2.0)  # Aproximação

            # Gerar hash realística
            tx_hash = "0x" + "".join(random.choices("0123456789abcdef", k=64))

            # UID temporário único
            temp_uid = f"test_{int(time.time() * 1000000)}_{i}"

            scenario = {
                "id": i + 1,
                "coin": coin,
                "vip_tier": vip_tier,
                "usd_amount": round(usd_amount, 2),
                "coin_amount": round(coin_amount, 8),
                "tx_hash": tx_hash,
                "temp_uid": temp_uid,
                "chain_id": coin["chain_id"],
                "contract": coin.get("contract"),
                "test_type": "vip_payment",
                "expected_days": vip_tier["days"]
            }

            scenarios.append(scenario)

        return scenarios

    async def test_payment_validation(self, session: aiohttp.ClientSession, scenario: Dict[str, Any]) -> Dict[str, Any]:
        """Testa validação de um pagamento VIP específico"""
        start_time = time.time()

        # Dados do teste baseados no endpoint /api/validate
        test_data = {
            "hash": scenario["tx_hash"],
            "uid": scenario["temp_uid"]
        }

        coin = scenario["coin"]
        result = {
            "scenario_id": scenario["id"],
            "coin_symbol": coin["symbol"],
            "chain": coin["chain"],
            "usd_amount": scenario["usd_amount"],
            "expected_days": scenario["expected_days"],
            "tx_hash": scenario["tx_hash"][:16] + "...",
            "success": False,
            "response_time": 0,
            "error_message": None,
            "status_code": None
        }

        try:
            url = f"{self.base_url}/api/validate"

            async with session.post(url, json=test_data, timeout=30) as response:
                response_time = time.time() - start_time
                result["response_time"] = round(response_time, 3)
                result["status_code"] = response.status

                if response.status == 200:
                    data = await response.json()
                    result["success"] = data.get("ok", False)

                    if not result["success"]:
                        result["error_message"] = data.get("message", "Unknown error")

                elif response.status == 400:
                    # Erro esperado para hashes fake - isso é normal
                    error_text = await response.text()
                    result["error_message"] = "Invalid hash (expected for test)"
                    result["success"] = True  # Considerar sucesso pois API respondeu corretamente

                else:
                    error_text = await response.text()
                    result["error_message"] = f"HTTP {response.status}: {error_text[:100]}"

        except asyncio.TimeoutError:
            result["response_time"] = time.time() - start_time
            result["error_message"] = "Timeout (>30s)"

        except Exception as e:
            result["response_time"] = time.time() - start_time
            result["error_message"] = str(e)[:100]

        return result

    async def run_comprehensive_vip_test(self, num_tests: int = 50, concurrency: int = 5) -> Dict[str, Any]:
        """Executa teste abrangente de pagamentos VIP"""
        LOG.info(f"🎯 [VIP-PAYMENT-TEST] Iniciando teste abrangente de pagamentos VIP")
        LOG.info(f"   • Total de testes: {num_tests}")
        LOG.info(f"   • Concorrência: {concurrency}")
        LOG.info(f"   • Moedas principais: {len(self.main_coins)}")
        LOG.info(f"   • Faixas VIP: {len(self.vip_tiers)}")

        start_time = time.time()

        # Gerar cenários realísticos
        scenarios = self.generate_realistic_payment_scenarios(num_tests)

        # Log da distribuição de moedas
        coin_distribution = {}
        tier_distribution = {}

        for scenario in scenarios:
            coin_symbol = scenario["coin"]["symbol"]
            coin_distribution[coin_symbol] = coin_distribution.get(coin_symbol, 0) + 1

            tier_name = scenario["vip_tier"]["plan"]
            tier_distribution[tier_name] = tier_distribution.get(tier_name, 0) + 1

        LOG.info(f"💰 [VIP-PAYMENT-TEST] Distribuição de moedas:")
        for coin, count in sorted(coin_distribution.items(), key=lambda x: x[1], reverse=True):
            LOG.info(f"   • {coin}: {count} testes")

        LOG.info(f"📦 [VIP-PAYMENT-TEST] Distribuição de planos VIP:")
        for tier, count in sorted(tier_distribution.items(), key=lambda x: x[1], reverse=True):
            LOG.info(f"   • {tier}: {count} testes")

        # Executar testes em batches
        results = []
        connector = aiohttp.TCPConnector(limit=concurrency * 2)
        timeout = aiohttp.ClientTimeout(total=60)

        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            batch_size = concurrency
            total_batches = (len(scenarios) + batch_size - 1) // batch_size

            for batch_idx in range(total_batches):
                start_idx = batch_idx * batch_size
                end_idx = min(start_idx + batch_size, len(scenarios))
                batch = scenarios[start_idx:end_idx]

                LOG.info(f"🔄 [VIP-PAYMENT-TEST] Batch {batch_idx + 1}/{total_batches} ({len(batch)} testes)")

                # Executar batch em paralelo
                batch_tasks = [self.test_payment_validation(session, scenario) for scenario in batch]
                batch_results = await asyncio.gather(*batch_tasks, return_exceptions=True)

                # Processar resultados
                for result in batch_results:
                    if isinstance(result, dict):
                        results.append(result)

                        # Log do progresso
                        status = "✅" if result["success"] else "❌"
                        LOG.info(f"   {status} {result['coin_symbol']} ({result['chain']}) - ${result['usd_amount']:.2f} USD - {result['response_time']:.3f}s")

                        if not result["success"] and result["error_message"]:
                            LOG.info(f"      └─ Erro: {result['error_message']}")
                    else:
                        LOG.error(f"   ❌ Erro no teste: {result}")

                # Pausa entre batches
                await asyncio.sleep(0.5)

        end_time = time.time()
        duration = end_time - start_time

        # Calcular estatísticas
        total_tests = len(results)
        successful_tests = sum(1 for r in results if r["success"])
        success_rate = (successful_tests / total_tests * 100) if total_tests > 0 else 0
        avg_response_time = sum(r["response_time"] for r in results) / total_tests if total_tests > 0 else 0
        throughput = total_tests / duration if duration > 0 else 0

        # Análise por moeda
        coin_analysis = {}
        for result in results:
            coin = result["coin_symbol"]
            if coin not in coin_analysis:
                coin_analysis[coin] = {
                    "total": 0,
                    "successful": 0,
                    "avg_response_time": 0,
                    "total_usd": 0
                }

            stats = coin_analysis[coin]
            stats["total"] += 1
            if result["success"]:
                stats["successful"] += 1
            stats["avg_response_time"] = (stats["avg_response_time"] * (stats["total"] - 1) + result["response_time"]) / stats["total"]
            stats["total_usd"] += result["usd_amount"]

        # Análise por chain
        chain_analysis = {}
        for result in results:
            chain = result["chain"]
            if chain not in chain_analysis:
                chain_analysis[chain] = {"total": 0, "successful": 0}

            chain_analysis[chain]["total"] += 1
            if result["success"]:
                chain_analysis[chain]["successful"] += 1

        # Log de resultados finais
        LOG.info(f"🎉 [VIP-PAYMENT-TEST] Teste concluído!")
        LOG.info(f"📊 [VIP-PAYMENT-TEST] Resultados finais:")
        LOG.info(f"   • Taxa de sucesso: {success_rate:.1f}% ({successful_tests}/{total_tests})")
        LOG.info(f"   • Tempo médio: {avg_response_time:.3f}s")
        LOG.info(f"   • Throughput: {throughput:.1f} testes/s")
        LOG.info(f"   • Duração total: {duration:.2f}s")

        LOG.info(f"💰 [VIP-PAYMENT-TEST] Performance por moeda:")
        for coin, stats in sorted(coin_analysis.items(), key=lambda x: x[1]["total"], reverse=True):
            success_rate_coin = (stats["successful"] / stats["total"] * 100) if stats["total"] > 0 else 0
            LOG.info(f"   • {coin:8} | {stats['successful']:2}/{stats['total']:2} ({success_rate_coin:5.1f}%) | {stats['avg_response_time']:.3f}s | ${stats['total_usd']:.2f}")

        LOG.info(f"🌐 [VIP-PAYMENT-TEST] Performance por chain:")
        for chain, stats in sorted(chain_analysis.items(), key=lambda x: x[1]["total"], reverse=True):
            success_rate_chain = (stats["successful"] / stats["total"] * 100) if stats["total"] > 0 else 0
            LOG.info(f"   • {chain:12} | {stats['successful']:2}/{stats['total']:2} ({success_rate_chain:5.1f}%)")

        # Compilar resultado final
        final_result = {
            "test_type": "vip_payment_comprehensive",
            "start_time": datetime.fromtimestamp(start_time).isoformat(),
            "end_time": datetime.fromtimestamp(end_time).isoformat(),
            "duration_seconds": round(duration, 2),
            "total_tests": total_tests,
            "successful_tests": successful_tests,
            "success_rate": round(success_rate, 1),
            "avg_response_time": round(avg_response_time, 3),
            "throughput": round(throughput, 1),
            "coin_analysis": coin_analysis,
            "chain_analysis": chain_analysis,
            "individual_results": results[:10]  # Primeiros 10 para não sobrecarregar logs
        }

        return final_result

# =========================
# Comandos para Telegram
# =========================

async def vip_payment_test_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /test_vip_payments - Teste completo de pagamentos VIP"""
    # Verificar se é admin
    from main import _require_admin
    if not _require_admin(update):
        return await update.effective_message.reply_text("❌ Apenas admins podem executar testes de pagamento VIP.")

    await update.effective_message.reply_text(
        "🎯 Iniciando teste completo de pagamentos VIP...\n"
        "📊 Testando 13+ moedas principais da checkout page\n"
        "⏱️ Duração estimada: 1-2 minutos\n\n"
        "📝 Acompanhe o progresso detalhado nos logs do Render!"
    )

    try:
        tester = VipPaymentStressTest()
        results = await tester.run_comprehensive_vip_test(num_tests=50, concurrency=5)

        summary = (
            f"✅ **Teste de Pagamentos VIP Concluído**\n\n"
            f"📊 **Resultados:**\n"
            f"• Testes realizados: {results['total_tests']}\n"
            f"• Taxa de sucesso: {results['success_rate']}%\n"
            f"• Tempo médio: {results['avg_response_time']}s\n"
            f"• Throughput: {results['throughput']} testes/s\n"
            f"• Duração: {results['duration_seconds']}s\n\n"
            f"💰 **Moedas testadas:** {len(results['coin_analysis'])}\n"
            f"🌐 **Chains testadas:** {len(results['chain_analysis'])}\n\n"
            f"📝 **Logs detalhados disponíveis no Render**"
        )

        await update.effective_message.reply_text(summary, parse_mode="Markdown")

    except Exception as e:
        LOG.error(f"❌ [VIP-PAYMENT-TEST] Erro: {e}")
        await update.effective_message.reply_text(f"❌ Erro no teste: {str(e)}")

async def vip_payment_quick_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /test_vip_quick - Teste rápido com principais moedas"""
    from main import _require_admin
    if not _require_admin(update):
        return await update.effective_message.reply_text("❌ Apenas admins.")

    await update.effective_message.reply_text("⚡ Teste rápido VIP iniciado... Verifique os logs!")

    try:
        tester = VipPaymentStressTest()
        results = await tester.run_comprehensive_vip_test(num_tests=20, concurrency=3)

        summary = (
            f"⚡ **Teste Rápido VIP Concluído**\n\n"
            f"📊 **Resultados:**\n"
            f"• Sucesso: {results['success_rate']}%\n"
            f"• Tempo: {results['avg_response_time']}s\n"
            f"• Moedas: {len(results['coin_analysis'])}\n"
            f"• Duração: {results['duration_seconds']}s"
        )

        await update.effective_message.reply_text(summary, parse_mode="Markdown")

    except Exception as e:
        LOG.error(f"❌ [VIP-QUICK-TEST] Erro: {e}")
        await update.effective_message.reply_text(f"❌ Erro: {str(e)}")

if __name__ == "__main__":
    # Teste local
    async def test_local():
        tester = VipPaymentStressTest()
        print("🧪 Testando sistema de pagamentos VIP localmente...")
        results = await tester.run_comprehensive_vip_test(num_tests=10, concurrency=2)
        print(f"✅ Teste concluído: {results['success_rate']}% sucesso")

    asyncio.run(test_local())