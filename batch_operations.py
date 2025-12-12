# batch_operations.py
import asyncio
import logging
from typing import List, Dict, Any, Optional, Callable, Tuple
from datetime import datetime
from dataclasses import dataclass

LOG = logging.getLogger(__name__)

@dataclass
class BatchResult:
    """Resultado de uma operação em batch"""
    success_count: int
    failure_count: int
    total_count: int
    errors: List[str]
    results: List[Any]
    execution_time: float

class BatchProcessor:
    """Sistema de processamento em lote otimizado"""

    def __init__(self,
                 max_concurrent: int = 20,
                 batch_size: int = 100,
                 delay_between_batches: float = 0.1):
        self.max_concurrent = max_concurrent
        self.batch_size = batch_size
        self.delay_between_batches = delay_between_batches
        self.semaphore = asyncio.Semaphore(max_concurrent)

    async def process_batch(self,
                           items: List[Any],
                           processor_func: Callable,
                           progress_callback: Optional[Callable] = None) -> BatchResult:
        """Processa uma lista de itens em lotes otimizados"""
        start_time = datetime.now()
        results = []
        errors = []
        success_count = 0
        failure_count = 0

        # Dividir em lotes
        batches = [items[i:i + self.batch_size] for i in range(0, len(items), self.batch_size)]
        total_batches = len(batches)

        LOG.info(f"Processando {len(items)} itens em {total_batches} lotes (max {self.max_concurrent} concurrent)")

        for batch_idx, batch in enumerate(batches):
            batch_start = datetime.now()

            # Processar lote com concorrência limitada
            batch_tasks = []
            for item in batch:
                task = self._process_single_item(processor_func, item)
                batch_tasks.append(task)

            # Executar lote
            batch_results = await asyncio.gather(*batch_tasks, return_exceptions=True)

            # Processar resultados do lote
            for item, result in zip(batch, batch_results):
                if isinstance(result, Exception):
                    failure_count += 1
                    error_msg = f"Item {item}: {str(result)}"
                    errors.append(error_msg)
                    LOG.debug(f"Batch error: {error_msg}")
                else:
                    success_count += 1
                    results.append(result)

            batch_time = (datetime.now() - batch_start).total_seconds()
            LOG.debug(f"Lote {batch_idx + 1}/{total_batches} concluído em {batch_time:.2f}s")

            # Callback de progresso
            if progress_callback:
                progress = (batch_idx + 1) / total_batches * 100
                await progress_callback(progress, batch_idx + 1, total_batches)

            # Delay entre lotes para evitar sobrecarga
            if batch_idx < total_batches - 1:
                await asyncio.sleep(self.delay_between_batches)

        execution_time = (datetime.now() - start_time).total_seconds()

        LOG.info(f"Batch processamento concluído: {success_count} sucessos, {failure_count} falhas em {execution_time:.2f}s")

        return BatchResult(
            success_count=success_count,
            failure_count=failure_count,
            total_count=len(items),
            errors=errors[:50],  # Limitar erros para não sobrecarregar
            results=results,
            execution_time=execution_time
        )

    async def _process_single_item(self, processor_func: Callable, item: Any) -> Any:
        """Processa um item individual com semáforo"""
        async with self.semaphore:
            try:
                if asyncio.iscoroutinefunction(processor_func):
                    return await processor_func(item)
                else:
                    return processor_func(item)
            except Exception as e:
                raise e

# Instância global do processador
batch_processor = BatchProcessor(max_concurrent=50, batch_size=200)

# =========================
# OPERAÇÕES ESPECÍFICAS DO BOT
# =========================

async def batch_send_messages(recipients: List[Tuple[int, str]],
                             progress_callback: Optional[Callable] = None) -> BatchResult:
    """Envia mensagens em lote para múltiplos usuários"""
    from main import application

    async def send_single_message(recipient_data: Tuple[int, str]) -> Dict[str, Any]:
        user_id, message = recipient_data
        try:
            await application.bot.send_message(chat_id=user_id, text=message, parse_mode="HTML")
            return {"user_id": user_id, "status": "sent"}
        except Exception as e:
            raise Exception(f"Failed to send to {user_id}: {e}")

    return await batch_processor.process_batch(
        recipients,
        send_single_message,
        progress_callback
    )

async def batch_validate_payments(payment_hashes: List[str],
                                 progress_callback: Optional[Callable] = None) -> BatchResult:
    """Valida múltiplos pagamentos em paralelo"""
    from payments import resolve_payment_usd_autochain

    async def validate_single_payment(tx_hash: str) -> Dict[str, Any]:
        try:
            result = await resolve_payment_usd_autochain(tx_hash, tg_uid=None, username=None)
            return {"tx_hash": tx_hash, "result": result}
        except Exception as e:
            raise Exception(f"Validation failed for {tx_hash}: {e}")

    return await batch_processor.process_batch(
        payment_hashes,
        validate_single_payment,
        progress_callback
    )

async def batch_update_vip_status(user_data: List[Tuple[int, bool, Optional[datetime]]],
                                 progress_callback: Optional[Callable] = None) -> BatchResult:
    """Atualiza status VIP de múltiplos usuários em lote"""
    from main import SessionLocal, VipMembership

    async def update_single_vip(vip_data: Tuple[int, bool, Optional[datetime]]) -> Dict[str, Any]:
        user_id, active, expires_at = vip_data
        try:
            with SessionLocal() as s:
                vip = s.query(VipMembership).filter(VipMembership.user_id == user_id).first()
                if vip:
                    vip.active = active
                    if expires_at:
                        vip.expires_at = expires_at
                    s.commit()
                    return {"user_id": user_id, "status": "updated"}
                else:
                    return {"user_id": user_id, "status": "not_found"}
        except Exception as e:
            raise Exception(f"Failed to update VIP for {user_id}: {e}")

    return await batch_processor.process_batch(
        user_data,
        update_single_vip,
        progress_callback
    )

async def batch_send_pack_previews(pack_data: List[Dict[str, Any]],
                                  progress_callback: Optional[Callable] = None) -> BatchResult:
    """Envia previews de packs para múltiplos chats"""

    async def send_single_preview(pack_info: Dict[str, Any]) -> Dict[str, Any]:
        # Implementar lógica de envio de preview
        pack_id = pack_info['pack_id']
        target_chat_id = pack_info['target_chat_id']

        try:
            # Aqui seria a lógica real de envio de preview
            # Por exemplo, chamar a função existente de envio
            await asyncio.sleep(0.1)  # Simular processamento
            return {"pack_id": pack_id, "chat_id": target_chat_id, "status": "sent"}
        except Exception as e:
            raise Exception(f"Failed to send preview for pack {pack_id}: {e}")

    return await batch_processor.process_batch(
        pack_data,
        send_single_preview,
        progress_callback
    )

# =========================
# BATCH OPERATIONS PARA DATABASE
# =========================

class DatabaseBatchProcessor:
    """Processador especial para operações de banco em lote"""

    @staticmethod
    async def batch_insert(session, model_class, data_list: List[Dict[str, Any]]) -> int:
        """Insere múltiplos registros de uma vez"""
        try:
            objects = [model_class(**data) for data in data_list]
            session.add_all(objects)
            session.commit()
            return len(objects)
        except Exception as e:
            session.rollback()
            raise e

    @staticmethod
    async def batch_update(session, model_class, updates: List[Dict[str, Any]], key_field: str = 'id') -> int:
        """Atualiza múltiplos registros com base numa chave"""
        try:
            updated_count = 0
            for update_data in updates:
                key_value = update_data.pop(key_field)
                query = session.query(model_class).filter(getattr(model_class, key_field) == key_value)
                result = query.update(update_data)
                updated_count += result

            session.commit()
            return updated_count
        except Exception as e:
            session.rollback()
            raise e

    @staticmethod
    async def batch_delete(session, model_class, ids: List[Any], key_field: str = 'id') -> int:
        """Deleta múltiplos registros"""
        try:
            query = session.query(model_class).filter(getattr(model_class, key_field).in_(ids))
            deleted_count = query.count()
            query.delete(synchronize_session=False)
            session.commit()
            return deleted_count
        except Exception as e:
            session.rollback()
            raise e

# =========================
# FUNÇÕES DE CONVENIÊNCIA
# =========================

async def bulk_notify_vip_expiration(expiring_vips: List[Dict[str, Any]]) -> BatchResult:
    """Notifica múltiplos VIPs sobre expiração em lote"""

    # Preparar dados para batch
    recipients = []
    for vip_data in expiring_vips:
        user_id = vip_data['user_id']
        days_left = vip_data['days_left']

        if days_left <= 1:
            message = "⚠️ Seu VIP expira em menos de 24 horas! Renove agora para continuar aproveitando o conteúdo exclusivo."
        elif days_left <= 3:
            message = f"⚠️ Seu VIP expira em {days_left} dias. Não perca o acesso ao conteúdo exclusivo!"
        else:
            message = f"📅 Lembrete: Seu VIP expira em {days_left} dias."

        recipients.append((user_id, message))

    return await batch_send_messages(recipients)

async def bulk_process_pending_payments(payment_list: List[str]) -> BatchResult:
    """Processa múltiplos pagamentos pendentes"""

    async def progress_callback(progress: float, current: int, total: int):
        LOG.info(f"Progresso validação pagamentos: {progress:.1f}% ({current}/{total})")

    return await batch_validate_payments(payment_list, progress_callback)

async def bulk_cleanup_expired_data(days_old: int = 30) -> Dict[str, int]:
    """Remove dados antigos em lote"""
    from main import SessionLocal, Payment, VipNotification
    from datetime import timedelta

    cutoff_date = datetime.now() - timedelta(days=days_old)
    results = {}

    with SessionLocal() as s:
        # Limpar pagamentos antigos rejeitados
        old_payments = s.query(Payment).filter(
            Payment.status == 'rejected',
            Payment.created_at < cutoff_date
        ).count()

        s.query(Payment).filter(
            Payment.status == 'rejected',
            Payment.created_at < cutoff_date
        ).delete()

        results['payments_cleaned'] = old_payments

        # Limpar notificações antigas
        old_notifications = s.query(VipNotification).filter(
            VipNotification.created_at < cutoff_date
        ).count()

        s.query(VipNotification).filter(
            VipNotification.created_at < cutoff_date
        ).delete()

        results['notifications_cleaned'] = old_notifications

        s.commit()

    LOG.info(f"Limpeza em lote concluída: {results}")
    return results

# =========================
# MONITORING E MÉTRICAS
# =========================

def get_batch_processor_stats() -> Dict[str, Any]:
    """Retorna estatísticas do processador de lotes"""
    return {
        "max_concurrent": batch_processor.max_concurrent,
        "batch_size": batch_processor.batch_size,
        "delay_between_batches": batch_processor.delay_between_batches,
        "semaphore_available": batch_processor.semaphore._value,
        "semaphore_total": batch_processor.max_concurrent
    }