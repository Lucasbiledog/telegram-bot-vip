# queue_system.py
import asyncio
import json
import logging
from typing import Any, Callable, Dict, Optional
from datetime import datetime
from enum import Enum

LOG = logging.getLogger(__name__)

class QueuePriority(Enum):
    LOW = 1
    NORMAL = 2
    HIGH = 3
    CRITICAL = 4

class TaskStatus(Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRY = "retry"

class AsyncTask:
    def __init__(self,
                 task_id: str,
                 task_type: str,
                 data: Dict[str, Any],
                 priority: QueuePriority = QueuePriority.NORMAL,
                 max_retries: int = 3):
        self.task_id = task_id
        self.task_type = task_type
        self.data = data
        self.priority = priority
        self.max_retries = max_retries
        self.retries = 0
        self.status = TaskStatus.PENDING
        self.created_at = datetime.now()
        self.started_at = None
        self.completed_at = None
        self.error_message = None

    def to_dict(self):
        return {
            'task_id': self.task_id,
            'task_type': self.task_type,
            'data': self.data,
            'priority': self.priority.value,
            'max_retries': self.max_retries,
            'retries': self.retries,
            'status': self.status.value,
            'created_at': self.created_at.isoformat(),
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'error_message': self.error_message
        }

class AsyncQueueManager:
    """Sistema de filas assíncronas para processar tarefas em background"""

    def __init__(self, max_workers: int = 10):
        self.queues = {
            QueuePriority.CRITICAL: asyncio.Queue(),
            QueuePriority.HIGH: asyncio.Queue(),
            QueuePriority.NORMAL: asyncio.Queue(),
            QueuePriority.LOW: asyncio.Queue(),
        }
        self.task_handlers: Dict[str, Callable] = {}
        self.workers = []
        self.max_workers = max_workers
        self.running = False
        self.stats = {
            'tasks_processed': 0,
            'tasks_failed': 0,
            'tasks_retried': 0,
            'active_workers': 0
        }

    def register_handler(self, task_type: str, handler: Callable):
        """Registra handler para um tipo de tarefa"""
        self.task_handlers[task_type] = handler
        LOG.info(f"Handler registrado para tipo de tarefa: {task_type}")

    async def enqueue_task(self,
                          task_type: str,
                          data: Dict[str, Any],
                          priority: QueuePriority = QueuePriority.NORMAL,
                          task_id: Optional[str] = None,
                          max_retries: int = 3) -> str:
        """Adiciona tarefa na fila"""

        if task_id is None:
            task_id = f"{task_type}_{datetime.now().timestamp()}_{id(data)}"

        task = AsyncTask(
            task_id=task_id,
            task_type=task_type,
            data=data,
            priority=priority,
            max_retries=max_retries
        )

        await self.queues[priority].put(task)
        LOG.debug(f"Tarefa enfileirada: {task_id} ({task_type}) - Priority: {priority.name}")
        return task_id

    async def _process_task(self, task: AsyncTask) -> bool:
        """Processa uma tarefa individual"""
        if task.task_type not in self.task_handlers:
            LOG.error(f"Handler não encontrado para tipo: {task.task_type}")
            return False

        task.status = TaskStatus.PROCESSING
        task.started_at = datetime.now()

        try:
            handler = self.task_handlers[task.task_type]
            await handler(task.data)

            task.status = TaskStatus.COMPLETED
            task.completed_at = datetime.now()
            self.stats['tasks_processed'] += 1

            LOG.debug(f"Tarefa concluída: {task.task_id}")
            return True

        except Exception as e:
            task.error_message = str(e)
            task.retries += 1

            if task.retries < task.max_retries:
                task.status = TaskStatus.RETRY
                # Re-enfileirar com prioridade menor
                retry_priority = QueuePriority.LOW
                await self.queues[retry_priority].put(task)
                self.stats['tasks_retried'] += 1
                LOG.warning(f"Tarefa falhou, tentativa {task.retries}/{task.max_retries}: {task.task_id} - {e}")
            else:
                task.status = TaskStatus.FAILED
                task.completed_at = datetime.now()
                self.stats['tasks_failed'] += 1
                LOG.error(f"Tarefa falhou definitivamente: {task.task_id} - {e}")

            return False

    async def _worker(self, worker_id: int):
        """Worker para processar tarefas"""
        LOG.info(f"Worker {worker_id} iniciado")
        self.stats['active_workers'] += 1

        try:
            while self.running:
                task = None

                # Processar filas por prioridade (CRITICAL primeiro)
                for priority in [QueuePriority.CRITICAL, QueuePriority.HIGH,
                               QueuePriority.NORMAL, QueuePriority.LOW]:
                    try:
                        task = await asyncio.wait_for(
                            self.queues[priority].get(),
                            timeout=1.0
                        )
                        break
                    except asyncio.TimeoutError:
                        continue

                if task is None:
                    continue

                await self._process_task(task)
                self.queues[task.priority].task_done()

        except Exception as e:
            LOG.error(f"Worker {worker_id} erro: {e}")
        finally:
            self.stats['active_workers'] -= 1
            LOG.info(f"Worker {worker_id} finalizado")

    async def start(self):
        """Inicia o sistema de filas"""
        if self.running:
            LOG.warning("Queue manager já está rodando")
            return

        self.running = True

        # Criar workers
        for i in range(self.max_workers):
            worker = asyncio.create_task(self._worker(i))
            self.workers.append(worker)

        LOG.info(f"Queue manager iniciado com {self.max_workers} workers")

    async def stop(self):
        """Para o sistema de filas"""
        if not self.running:
            return

        self.running = False

        # Aguardar workers terminarem
        if self.workers:
            await asyncio.gather(*self.workers, return_exceptions=True)
            self.workers.clear()

        LOG.info("Queue manager parado")

    def get_stats(self) -> Dict[str, Any]:
        """Retorna estatísticas do sistema"""
        queue_sizes = {
            priority.name: self.queues[priority].qsize()
            for priority in QueuePriority
        }

        return {
            **self.stats,
            'queue_sizes': queue_sizes,
            'total_queued': sum(queue_sizes.values()),
            'running': self.running
        }

# Instância global do queue manager
queue_manager = AsyncQueueManager(max_workers=20)  # Mais workers para alta concorrência

# Handlers específicos para tarefas do bot
async def payment_validation_handler(data: Dict[str, Any]):
    """Handler para validação de pagamentos em background"""
    from payments import resolve_payment_usd_autochain

    tx_hash = data['tx_hash']
    user_id = data['user_id']
    username = data.get('username')

    LOG.info(f"Validando pagamento em background: {tx_hash}")

    result = await resolve_payment_usd_autochain(
        tx_hash=tx_hash,
        tg_uid=user_id,
        username=username
    )

    LOG.info(f"Pagamento validado: {tx_hash} - Resultado: {result}")
    return result

async def pack_sending_handler(data: Dict[str, Any]):
    """Handler para envio de packs em background"""
    pack_id = data['pack_id']
    tier = data['tier']
    target_chat_id = data['target_chat_id']

    LOG.info(f"Enviando pack {pack_id} para {tier}")

    # Import local para evitar circular import
    from main import enviar_pack_job, application

    # Simular context para o job
    class MockContext:
        def __init__(self):
            self.application = application

    context = MockContext()
    result = await enviar_pack_job(context, tier, target_chat_id)

    LOG.info(f"Pack {pack_id} enviado: {result}")
    return result

async def vip_notification_handler(data: Dict[str, Any]):
    """Handler para notificações VIP em background"""
    user_id = data['user_id']
    notification_type = data['notification_type']
    message = data.get('message', '')

    LOG.info(f"Enviando notificação VIP para {user_id}: {notification_type}")

    from main import dm

    success = await dm(user_id, message)

    LOG.info(f"Notificação VIP enviada para {user_id}: {'✅' if success else '❌'}")
    return success

# Registrar handlers
async def init_queue_system():
    """Inicializa sistema de filas com handlers"""
    queue_manager.register_handler('payment_validation', payment_validation_handler)
    queue_manager.register_handler('pack_sending', pack_sending_handler)
    queue_manager.register_handler('vip_notification', vip_notification_handler)

    await queue_manager.start()
    LOG.info("Sistema de filas inicializado com sucesso")

# Funções de conveniência
async def queue_payment_validation(tx_hash: str, user_id: int, username: str = None):
    """Enfileira validação de pagamento"""
    return await queue_manager.enqueue_task(
        'payment_validation',
        {'tx_hash': tx_hash, 'user_id': user_id, 'username': username},
        priority=QueuePriority.HIGH
    )

async def queue_pack_sending(pack_id: int, tier: str, target_chat_id: int):
    """Enfileira envio de pack"""
    return await queue_manager.enqueue_task(
        'pack_sending',
        {'pack_id': pack_id, 'tier': tier, 'target_chat_id': target_chat_id},
        priority=QueuePriority.NORMAL
    )

async def queue_vip_notification(user_id: int, notification_type: str, message: str):
    """Enfileira notificação VIP"""
    return await queue_manager.enqueue_task(
        'vip_notification',
        {'user_id': user_id, 'notification_type': notification_type, 'message': message},
        priority=QueuePriority.HIGH
    )