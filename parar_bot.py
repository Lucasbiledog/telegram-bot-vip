"""
Script para Parar Processos do Bot
===================================
Para todos os processos Python relacionados ao bot
"""

import sys
import os


def stop_bot_processes():
    """Para todos os processos do bot"""
    print("\n" + "=" * 70)
    print("  PARAR PROCESSOS DO BOT")
    print("=" * 70 + "\n")

    try:
        import psutil

        found = []
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                if 'python' in proc.info['name'].lower():
                    cmdline = proc.info['cmdline']
                    if cmdline:
                        cmd_str = ' '.join(cmdline)
                        # Verificar se é relacionado ao bot
                        if any(script in cmd_str for script in ['main.py', 'auto_sender.py', 'keep_alive.py']):
                            found.append({
                                'pid': proc.info['pid'],
                                'cmd': cmd_str,
                                'proc': proc
                            })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        if not found:
            print("✅ Nenhum processo do bot está rodando.")
            return

        print(f"Encontrados {len(found)} processo(s) do bot:\n")
        for i, proc_info in enumerate(found, 1):
            print(f"{i}. PID {proc_info['pid']}: {proc_info['cmd'][:70]}...")

        print("\n" + "=" * 70)
        print("Deseja parar TODOS estes processos?")
        choice = input("Digite 's' para sim ou 'n' para não [s]: ").strip().lower()

        if choice == '' or choice == 's':
            print("\n⏳ Parando processos...\n")
            for proc_info in found:
                try:
                    proc = proc_info['proc']
                    pid = proc_info['pid']
                    proc.terminate()  # Terminar graciosamente
                    proc.wait(timeout=5)  # Aguardar até 5 segundos
                    print(f"✅ Processo {pid} parado com sucesso")
                except psutil.TimeoutExpired:
                    # Se não parar, forçar
                    proc.kill()
                    print(f"⚠️ Processo {pid} forçado a parar (kill)")
                except Exception as e:
                    print(f"❌ Erro ao parar processo {pid}: {e}")

            print(f"\n✅ Todos os processos foram parados!")
            print(f"\nAgora você pode iniciar o bot novamente:")
            print(f"  python main.py")
            print(f"  ou")
            print(f"  python auto_sender.py")
        else:
            print("\n❌ Operação cancelada.")

    except ImportError:
        print("❌ Módulo 'psutil' não encontrado!")
        print("\nInstale com:")
        print("  pip install psutil")
        print("\nOu pare manualmente com Ctrl+C no terminal do bot")
    except Exception as e:
        print(f"❌ Erro: {e}")


def main():
    """Função principal"""
    stop_bot_processes()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n👋 Operação interrompida.")
    except Exception as e:
        print(f"\n❌ ERRO: {e}")
        import traceback
        traceback.print_exc()
