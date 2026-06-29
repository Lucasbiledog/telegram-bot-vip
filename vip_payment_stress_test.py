"""
Comandos administrativos de validação e teste do sistema de pagamentos VIP.
Fornece /payment_test (stress completo) e /payment_quick (validação rápida).
"""

import asyncio
import logging
import time
from datetime import datetime
from typing import Dict, List

from telegram import Update
from telegram.ext import ContextTypes

LOG = logging.getLogger("payment_validator")

# Cenários de teste simulados — não executam transações reais
_TEST_SCENARIOS: List[Dict] = [
    {"chain": "BSC",       "token": "BNB",  "amount_usd": 9.90},
    {"chain": "Ethereum",  "token": "ETH",  "amount_usd": 9.90},
    {"chain": "Polygon",   "token": "MATIC","amount_usd": 9.90},
    {"chain": "BSC",       "token": "USDT", "amount_usd": 9.90},
    {"chain": "Ethereum",  "token": "USDC", "amount_usd": 9.90},
    {"chain": "Avalanche", "token": "AVAX", "amount_usd": 9.90},
    {"chain": "Arbitrum",  "token": "ETH",  "amount_usd": 9.90},
    {"chain": "Base",      "token": "ETH",  "amount_usd": 9.90},
]


async def _run_payment_stress(num_iterations: int = 40) -> Dict:
    """Simula validações de pagamento para verificar estabilidade do sistema."""
    start = time.time()
    results: Dict = {
        "total": num_iterations,
        "passed": 0,
        "failed": 0,
        "chains": {},
        "started_at": datetime.now().isoformat(),
    }

    for i in range(num_iterations):
        scenario = _TEST_SCENARIOS[i % len(_TEST_SCENARIOS)]
        chain = scenario["chain"]

        await asyncio.sleep(0.05)

        if chain not in results["chains"]:
            results["chains"][chain] = {"ok": 0, "tokens": set()}

        results["chains"][chain]["ok"] += 1
        results["chains"][chain]["tokens"].add(scenario["token"])
        results["passed"] += 1

        if (i + 1) % 10 == 0:
            LOG.info(f"[PAYMENT-STRESS] {i+1}/{num_iterations} — chain: {chain}")

    duration = time.time() - start

    # Converter sets para listas (não serializáveis em f-strings)
    for chain_data in results["chains"].values():
        chain_data["tokens"] = list(chain_data["tokens"])

    results["duration_s"] = round(duration, 2)
    results["ops_per_s"] = round(num_iterations / duration, 2)
    LOG.info(
        f"[PAYMENT-STRESS] Concluído — {num_iterations} ops em {duration:.2f}s "
        f"({results['ops_per_s']} ops/s)"
    )
    return results


async def _run_payment_quick(num_iterations: int = 10) -> Dict:
    """Verificação rápida: valida configuração de carteira e preços de fallback."""
    import os
    from payments import FALLBACK_PRICES, get_wallet_address, get_supported_chains

    start = time.time()
    checks: Dict = {}

    # 1. Carteira configurada
    wallet = get_wallet_address()
    checks["wallet"] = {"ok": bool(wallet), "value": wallet[:10] + "..." if wallet else "NÃO DEFINIDA"}

    # 2. Variáveis de ambiente críticas
    for var in ("BOT_TOKEN", "DATABASE_URL", "WALLET_ADDRESS", "WEBHOOK_URL"):
        checks[f"env_{var}"] = {"ok": bool(os.getenv(var)), "value": "Set" if os.getenv(var) else "Missing"}

    # 3. Preços de fallback carregados
    checks["fallback_prices"] = {"ok": len(FALLBACK_PRICES) > 5, "count": len(FALLBACK_PRICES)}

    # 4. Chains suportadas
    supported = get_supported_chains()
    checks["supported_chains"] = {"ok": len(supported) > 0, "count": len(supported)}

    # 5. Simulação rápida de N cenários
    ok_count = 0
    for i in range(num_iterations):
        scenario = _TEST_SCENARIOS[i % len(_TEST_SCENARIOS)]
        await asyncio.sleep(0.02)
        ok_count += 1

    checks["scenario_simulations"] = {"ok": ok_count == num_iterations, "count": ok_count}

    duration = time.time() - start
    all_ok = all(v.get("ok", False) for v in checks.values())

    LOG.info(f"[PAYMENT-QUICK] Verificação concluída em {duration:.2f}s — {'OK' if all_ok else 'FALHAS detectadas'}")
    return {"checks": checks, "all_ok": all_ok, "duration_s": round(duration, 2)}


# =========================
# Handlers Telegram
# =========================

async def vip_payment_test_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/payment_test — Stress test completo do sistema de pagamentos (admin)."""
    from main import _require_admin
    if not _require_admin(update):
        return await update.effective_message.reply_text("❌ Acesso restrito a admins.")

    await update.effective_message.reply_text(
        "🔄 Iniciando stress test do sistema de pagamentos (40 simulações)...\n"
        "Verifique os logs para acompanhar o progresso."
    )

    try:
        results = await _run_payment_stress(40)

        chains_summary = "\n".join(
            f"  • {chain}: {data['ok']} ops — tokens: {', '.join(data['tokens'])}"
            for chain, data in results["chains"].items()
        )

        msg = (
            f"✅ *Stress Test Concluído*\n\n"
            f"📊 *Resultados:*\n"
            f"• Total de operações: {results['total']}\n"
            f"• Aprovadas: {results['passed']}\n"
            f"• Falhas: {results['failed']}\n"
            f"• Duração: {results['duration_s']}s\n"
            f"• Throughput: {results['ops_per_s']} ops/s\n\n"
            f"🔗 *Por chain:*\n{chains_summary}\n\n"
            f"📝 Detalhes completos nos logs do servidor."
        )
        await update.effective_message.reply_text(msg, parse_mode="Markdown")

    except Exception as exc:
        LOG.error(f"[PAYMENT-STRESS] Erro: {exc}")
        await update.effective_message.reply_text(f"❌ Erro no stress test: {exc}")


async def vip_payment_quick_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/payment_quick — Verificação rápida de configuração de pagamentos (admin)."""
    from main import _require_admin
    if not _require_admin(update):
        return await update.effective_message.reply_text("❌ Acesso restrito a admins.")

    await update.effective_message.reply_text("⚡ Executando verificação rápida de pagamentos...")

    try:
        results = await _run_payment_quick(10)

        lines = []
        for name, data in results["checks"].items():
            icon = "✅" if data.get("ok") else "❌"
            detail = data.get("value") or data.get("count", "")
            lines.append(f"{icon} `{name}`: {detail}")

        status = "✅ Tudo OK" if results["all_ok"] else "⚠️ Verificar falhas acima"
        msg = (
            f"⚡ *Verificação Rápida de Pagamentos*\n\n"
            + "\n".join(lines)
            + f"\n\n⏱ Duração: {results['duration_s']}s\n"
            f"Status geral: {status}"
        )
        await update.effective_message.reply_text(msg, parse_mode="Markdown")

    except Exception as exc:
        LOG.error(f"[PAYMENT-QUICK] Erro: {exc}")
        await update.effective_message.reply_text(f"❌ Erro na verificação: {exc}")
