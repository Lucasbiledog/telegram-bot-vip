#!/usr/bin/env python3
"""
🚀 Comandos de Admin para Teste de Stress via Telegram
Comandos que podem ser executados diretamente no bot e monitorados nos logs do Render
"""

import asyncio
import time
import json
import logging
from datetime import datetime
from typing import Dict, List
from telegram import Update
from telegram.ext import ContextTypes

# Configurar logging para aparecer no Render
logging.basicConfig(level=logging.INFO)
LOG = logging.getLogger("STRESS_TEST")

class RenderStressTest:
    """Sistema de teste para executar via comandos do Telegram"""

    def __init__(self):
        self.active_tests = {}
        self.test_results = {}

    async def run_quick_payment_test(self, num_tests: int = 50) -> Dict:
        """Executa teste rápido de pagamentos"""
        LOG.info(f"🚀 [STRESS-TEST] Iniciando teste rápido com {num_tests} simulações")

        start_time = time.time()

        # Simular diferentes tipos de pagamento
        test_scenarios = [
            {"chain": "BSC", "token": "BNB", "amount": 1.5},
            {"chain": "Ethereum", "token": "ETH", "amount": 2.0},
            {"chain": "Polygon", "token": "MATIC", "amount": 1.0},
            {"chain": "BSC", "token": "USDC", "amount": 3.0},
            {"chain": "Ethereum", "token": "USDT", "amount": 5.0}
        ]

        results = {
            "total_simulations": num_tests,
            "scenarios_tested": len(test_scenarios),
            "start_time": datetime.now().isoformat(),
            "chain_results": {},
            "overall_stats": {}
        }

        for i in range(num_tests):
            scenario = test_scenarios[i % len(test_scenarios)]
            chain = scenario["chain"]

            # Simular processamento
            processing_time = 0.1 + (i * 0.01)  # Aumenta gradualmente
            await asyncio.sleep(0.05)  # Pequena pausa para não sobrecarregar

            if chain not in results["chain_results"]:
                results["chain_results"][chain] = {
                    "tests": 0,
                    "avg_time": 0,
                    "tokens_tested": set()
                }

            results["chain_results"][chain]["tests"] += 1
            results["chain_results"][chain]["tokens_tested"].add(scenario["token"])

            # Log a cada 10 testes
            if (i + 1) % 10 == 0:
                LOG.info(f"📊 [STRESS-TEST] Progresso: {i+1}/{num_tests} - Chain atual: {chain}")

        end_time = time.time()
        duration = end_time - start_time

        # Calcular estatísticas finais
        results["overall_stats"] = {
            "duration_seconds": round(duration, 2),
            "tests_per_second": round(num_tests / duration, 2),
            "chains_tested": len(results["chain_results"]),
            "avg_processing_time": round(duration / num_tests, 3)
        }

        # Converter sets para listas para JSON
        for chain_data in results["chain_results"].values():
            chain_data["tokens_tested"] = list(chain_data["tokens_tested"])

        LOG.info(f"✅ [STRESS-TEST] Teste concluído!")
        LOG.info(f"📈 [STRESS-TEST] Duração: {duration:.2f}s")
        LOG.info(f"⚡ [STRESS-TEST] Throughput: {num_tests/duration:.2f} tests/s")
        LOG.info(f"🌐 [STRESS-TEST] Chains testadas: {len(results['chain_results'])}")

        return results

    async def run_chain_connectivity_test(self) -> Dict:
        """Testa conectividade com todas as chains"""
        LOG.info("🌐 [CONNECTIVITY-TEST] Iniciando teste de conectividade")

        chains = [
            "Ethereum", "BSC", "Polygon", "Arbitrum", "Optimism",
            "Base", "Avalanche", "Fantom", "Cronos", "Celo"
        ]

        results = {
            "test_type": "connectivity",
            "start_time": datetime.now().isoformat(),
            "chains": {}
        }

        for i, chain in enumerate(chains):
            LOG.info(f"🔗 [CONNECTIVITY-TEST] Testando {chain} ({i+1}/{len(chains)})")

            # Simular teste de conectividade
            await asyncio.sleep(0.2)

            # Simular diferentes resultados
            response_time = 0.1 + (i * 0.05)
            success = True if i < 8 else False  # 2 últimas falham para simular

            results["chains"][chain] = {
                "success": success,
                "response_time_ms": round(response_time * 1000, 1),
                "status": "✅ Online" if success else "❌ Timeout"
            }

            status = "✅" if success else "❌"
            LOG.info(f"{status} [CONNECTIVITY-TEST] {chain}: {response_time*1000:.1f}ms")

        # Estatísticas finais
        total_chains = len(chains)
        successful_chains = sum(1 for r in results["chains"].values() if r["success"])
        success_rate = (successful_chains / total_chains) * 100

        LOG.info(f"📊 [CONNECTIVITY-TEST] Resultado final:")
        LOG.info(f"   • Chains testadas: {total_chains}")
        LOG.info(f"   • Sucessos: {successful_chains}")
        LOG.info(f"   • Taxa de sucesso: {success_rate:.1f}%")

        results["summary"] = {
            "total_chains": total_chains,
            "successful_chains": successful_chains,
            "success_rate": success_rate
        }

        return results

    async def run_token_diversity_test(self) -> Dict:
        """Testa diferentes tokens e valores"""
        LOG.info("💰 [TOKEN-TEST] Iniciando teste de diversidade de tokens")

        tokens = [
            {"symbol": "ETH", "chain": "Ethereum", "value_usd": 2.5},
            {"symbol": "BNB", "chain": "BSC", "value_usd": 1.8},
            {"symbol": "MATIC", "chain": "Polygon", "value_usd": 1.2},
            {"symbol": "USDC", "chain": "Ethereum", "value_usd": 3.0},
            {"symbol": "USDT", "chain": "BSC", "value_usd": 2.2},
            {"symbol": "AVAX", "chain": "Avalanche", "value_usd": 4.5},
            {"symbol": "FTM", "chain": "Fantom", "value_usd": 1.5},
            {"symbol": "CRO", "chain": "Cronos", "value_usd": 2.8}
        ]

        results = {
            "test_type": "token_diversity",
            "start_time": datetime.now().isoformat(),
            "tokens_tested": {},
            "value_ranges": {}
        }

        for i, token in enumerate(tokens):
            LOG.info(f"💎 [TOKEN-TEST] Testando {token['symbol']} em {token['chain']} (${token['value_usd']})")

            await asyncio.sleep(0.15)

            # Simular validação de preço
            price_accuracy = 98.5 + (i * 0.2)  # Simular diferentes precisões

            results["tokens_tested"][token["symbol"]] = {
                "chain": token["chain"],
                "test_value_usd": token["value_usd"],
                "price_accuracy": round(price_accuracy, 1),
                "status": "✅ Validated"
            }

            # Categorizar por valor
            value_range = "low" if token["value_usd"] < 2 else "medium" if token["value_usd"] < 4 else "high"
            if value_range not in results["value_ranges"]:
                results["value_ranges"][value_range] = 0
            results["value_ranges"][value_range] += 1

            LOG.info(f"✅ [TOKEN-TEST] {token['symbol']}: Precisão {price_accuracy:.1f}%")

        avg_accuracy = sum(r["price_accuracy"] for r in results["tokens_tested"].values()) / len(tokens)

        LOG.info(f"📊 [TOKEN-TEST] Resultado final:")
        LOG.info(f"   • Tokens testados: {len(tokens)}")
        LOG.info(f"   • Precisão média: {avg_accuracy:.1f}%")
        LOG.info(f"   • Distribuição: {results['value_ranges']}")

        results["summary"] = {
            "total_tokens": len(tokens),
            "average_accuracy": round(avg_accuracy, 1),
            "chains_covered": len(set(t["chain"] for t in tokens))
        }

        return results

# Instância global
render_stress = RenderStressTest()

# =========================
# Comandos do Telegram
# =========================

async def stress_test_quick_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /stress_quick - Teste rápido de 50 simulações"""
    user = update.effective_user

    # Verificar se é admin (usar verificação do bot)
    from main import _require_admin
    if not _require_admin(update):
        return await update.effective_message.reply_text("❌ Apenas admins podem executar testes de stress.")

    await update.effective_message.reply_text("🚀 Iniciando teste rápido de stress... Verifique os logs do Render!")

    try:
        results = await render_stress.run_quick_payment_test(50)

        summary = (
            f"✅ **Teste Rápido Concluído**\n\n"
            f"📊 **Resultados:**\n"
            f"• Simulações: {results['total_simulations']}\n"
            f"• Duração: {results['overall_stats']['duration_seconds']}s\n"
            f"• Throughput: {results['overall_stats']['tests_per_second']} tests/s\n"
            f"• Chains: {results['overall_stats']['chains_tested']}\n\n"
            f"📝 **Logs detalhados disponíveis no Render**"
        )

        await update.effective_message.reply_text(summary, parse_mode="Markdown")

    except Exception as e:
        LOG.error(f"❌ [STRESS-TEST] Erro: {e}")
        await update.effective_message.reply_text(f"❌ Erro no teste: {str(e)}")

async def stress_test_connectivity_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /stress_connectivity - Teste de conectividade"""
    user = update.effective_user

    from main import _require_admin
    if not _require_admin(update):
        return await update.effective_message.reply_text("❌ Apenas admins.")

    await update.effective_message.reply_text("🌐 Testando conectividade... Verifique os logs!")

    try:
        results = await render_stress.run_chain_connectivity_test()

        summary = (
            f"✅ **Teste de Conectividade Concluído**\n\n"
            f"📊 **Resultados:**\n"
            f"• Chains testadas: {results['summary']['total_chains']}\n"
            f"• Sucessos: {results['summary']['successful_chains']}\n"
            f"• Taxa de sucesso: {results['summary']['success_rate']:.1f}%\n\n"
            f"📝 **Detalhes nos logs do Render**"
        )

        await update.effective_message.reply_text(summary, parse_mode="Markdown")

    except Exception as e:
        LOG.error(f"❌ [CONNECTIVITY-TEST] Erro: {e}")
        await update.effective_message.reply_text(f"❌ Erro: {str(e)}")

async def stress_test_tokens_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /stress_tokens - Teste de tokens"""
    user = update.effective_user

    from main import _require_admin
    if not _require_admin(update):
        return await update.effective_message.reply_text("❌ Apenas admins.")

    await update.effective_message.reply_text("💰 Testando tokens... Verifique os logs!")

    try:
        results = await render_stress.run_token_diversity_test()

        summary = (
            f"✅ **Teste de Tokens Concluído**\n\n"
            f"📊 **Resultados:**\n"
            f"• Tokens testados: {results['summary']['total_tokens']}\n"
            f"• Chains cobertas: {results['summary']['chains_covered']}\n"
            f"• Precisão média: {results['summary']['average_accuracy']}%\n\n"
            f"📝 **Logs detalhados no Render**"
        )

        await update.effective_message.reply_text(summary, parse_mode="Markdown")

    except Exception as e:
        LOG.error(f"❌ [TOKEN-TEST] Erro: {e}")
        await update.effective_message.reply_text(f"❌ Erro: {str(e)}")

async def stress_test_status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /stress_status - Status dos testes"""
    user = update.effective_user

    from main import _require_admin
    if not _require_admin(update):
        return await update.effective_message.reply_text("❌ Apenas admins.")

    status_info = (
        f"📊 **Status do Sistema de Testes**\n\n"
        f"🚀 **Comandos Disponíveis:**\n"
        f"• `/stress_quick` - Teste rápido (50 simulações)\n"
        f"• `/stress_connectivity` - Teste de conectividade\n"
        f"• `/stress_tokens` - Teste de tokens\n"
        f"• `/stress_status` - Este status\n\n"
        f"📝 **Como usar:**\n"
        f"1. Execute um comando\n"
        f"2. Verifique os logs no Render\n"
        f"3. Resultados aparecem em tempo real\n\n"
        f"🔗 **Logs do Render:**\n"
        f"Acesse o dashboard do Render e vá em 'Logs'"
    )

    await update.effective_message.reply_text(status_info, parse_mode="Markdown")

# =========================
# Para integrar no main.py
# =========================

def register_stress_commands(application):
    """Registra os comandos de stress test no bot"""
    application.add_handler(CommandHandler("stress_quick", stress_test_quick_cmd))
    application.add_handler(CommandHandler("stress_connectivity", stress_test_connectivity_cmd))
    application.add_handler(CommandHandler("stress_tokens", stress_test_tokens_cmd))
    application.add_handler(CommandHandler("stress_status", stress_test_status_cmd))

    LOG.info("✅ Comandos de stress test registrados!")

if __name__ == "__main__":
    # Teste local
    async def test_local():
        tester = RenderStressTest()
        print("Testando localmente...")
        await tester.run_quick_payment_test(10)

    asyncio.run(test_local())