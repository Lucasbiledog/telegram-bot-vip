#!/usr/bin/env python3
"""
Script de inicialização robusta do bot Telegram
Mantém o bot sempre ativo com auto-restart em caso de falhas
"""
import os
import sys
import time
import logging
import subprocess
import signal
from datetime import datetime

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('bot_supervisor.log', encoding='utf-8')
    ]
)

class BotSupervisor:
    def __init__(self):
        self.process = None
        self.restart_count = 0
        self.max_restarts = 10  # Máximo de reinicializações por hora
        self.restart_times = []
        self.running = True
        
    def cleanup_old_restarts(self):
        """Remove reinicializações antigas (mais de 1 hora)"""
        now = datetime.now()
        self.restart_times = [
            restart_time for restart_time in self.restart_times 
            if (now - restart_time).seconds < 3600  # 1 hora
        ]
    
    def can_restart(self):
        """Verifica se pode reiniciar (limite de reinicializações por hora)"""
        self.cleanup_old_restarts()
        return len(self.restart_times) < self.max_restarts
    
    def start_bot(self):
        """Inicia o processo do bot"""
        try:
            logging.info("🚀 Iniciando bot Telegram...")
            self.process = subprocess.Popen(
                [sys.executable, "main.py"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True
            )
            logging.info(f"✅ Bot iniciado com PID: {self.process.pid}")
            return True
        except Exception as e:
            logging.error(f"❌ Erro ao iniciar bot: {e}")
            return False
    
    def monitor_bot(self):
        """Monitora o processo do bot"""
        if not self.process:
            return False
            
        # Verificar se o processo ainda está rodando
        return_code = self.process.poll()
        
        if return_code is None:
            # Processo ainda está rodando
            return True
        else:
            # Processo terminou
            logging.warning(f"🔄 Bot terminou com código: {return_code}")
            
            # Ler output restante
            try:
                output, _ = self.process.communicate(timeout=5)
                if output:
                    logging.info(f"Output final do bot: {output}")
            except subprocess.TimeoutExpired:
                self.process.kill()
                
            return False
    
    def restart_bot(self):
        """Reinicia o bot"""
        if not self.can_restart():
            logging.error("❌ Limite de reinicializações atingido por hora!")
            logging.error("⏰ Aguardando 1 hora antes de permitir novos restarts...")
            time.sleep(3600)  # Aguardar 1 hora
            return False
            
        logging.info("🔄 Reiniciando bot em 5 segundos...")
        time.sleep(5)
        
        self.restart_times.append(datetime.now())
        self.restart_count += 1
        
        return self.start_bot()
    
    def signal_handler(self, signum, frame):
        """Handler para sinais de sistema"""
        logging.info(f"📡 Recebido sinal {signum}")
        if signum == signal.SIGINT:
            logging.info("🛑 Ctrl+C detectado - parando supervisor...")
            self.running = False
            if self.process:
                self.process.terminate()
        elif signum == signal.SIGTERM:
            logging.info("🛑 SIGTERM detectado - parando supervisor...")
            self.running = False
            if self.process:
                self.process.terminate()
    
    def run(self):
        """Execução principal do supervisor"""
        # Configurar handlers de sinal
        signal.signal(signal.SIGINT, self.signal_handler)
        if hasattr(signal, 'SIGTERM'):
            signal.signal(signal.SIGTERM, self.signal_handler)
        
        logging.info("🤖 Bot Supervisor iniciado!")
        logging.info("📱 Para parar completamente, use Ctrl+C duas vezes")
        logging.info("🔄 O bot será reiniciado automaticamente em caso de falha")
        
        # Iniciar o bot pela primeira vez
        if not self.start_bot():
            logging.error("❌ Falha ao iniciar bot inicial!")
            return
        
        # Loop de monitoramento
        while self.running:
            try:
                time.sleep(5)  # Verificar a cada 5 segundos
                
                if not self.monitor_bot():
                    if self.running:  # Só reiniciar se não foi interrompido intencionalmente
                        logging.warning("⚠️ Bot parou unexpectadamente!")
                        if not self.restart_bot():
                            logging.error("❌ Falha ao reiniciar bot!")
                            break
                
            except KeyboardInterrupt:
                logging.info("🛑 Segunda interrupção detectada - parando definitivamente...")
                self.running = False
                if self.process:
                    self.process.terminate()
                break
            except Exception as e:
                logging.error(f"❌ Erro no supervisor: {e}")
                time.sleep(10)
        
        logging.info("🔚 Bot Supervisor finalizado")

if __name__ == "__main__":
    # Mudar para o diretório do script
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    
    supervisor = BotSupervisor()
    supervisor.run()