from __future__ import annotations

import argparse
import os
import sys
import threading
import time
import webbrowser
from services.coletor import iniciar_coletor
from services.extrator import extrair_dados_comunidade
from services.paths import get_data_dir, setup_environment
from services.storage import export_to_csv, get_db_path
from ui.server import start_server


def exportar_db_para_csv():
    caminho_db = get_db_path()

    if not os.path.exists(caminho_db):
        print(f"\nBanco não encontrado em: {caminho_db}")
        return

    nome_arquivo = (
        input("Digite o nome do arquivo CSV (ou Enter para usar export_messages.csv): ").strip()
        or "export_messages.csv"
    )
    destino = nome_arquivo

    if not os.path.isabs(destino):
        destino = str(get_data_dir() / destino)

    try:
        export_to_csv(destino, db_path=caminho_db)
        print(f"\nCSV exportado com sucesso em: {destino}")
    except Exception as e:
        print(f"\nErro ao exportar CSV: {e}")


def menu_cli():
    while True:
        print("\n" + "=" * 40)
        print("    ORQUESTRADOR DE AUTOMAÇÃO WHATSAPP (CLI)")
        print("=" * 40)
        print("1. Iniciar Servidor Web")
        print("2. Iniciar Coletor (Fazer Login / Escanear QR Code)")
        print("3. Iniciar Extrator (Coletar mensagens do Grupo)")
        print("4. Exportar banco para CSV")
        print("5. Sair")
        print("=" * 40)

        opcao = input("Escolha uma opção (1-5): ").strip()

        if opcao == "1":
            iniciar_servidor_web()
            break
        elif opcao == "2":
            print("\nIniciando coletor...")
            try:
                iniciar_coletor()
            except Exception as e:
                print(f"Erro ao executar o coletor: {e}")
        elif opcao == "3":
            print("\nConfigurando Extrator...")
            grupo = input("Digite o nome do grupo (ou Enter para usar o padrão): ").strip()
            comunidade = input("Digite o nome da comunidade (ou Enter para usar o padrão): ").strip()

            print("\nEscolha o tipo de filtro:")
            print("1. Por mês")
            print("2. Por dias")
            tipo_filtro_opcao = input("Digite a opção (1 ou 2): ").strip()

            if tipo_filtro_opcao == "1":
                tipo_filtro = "mes"
                meses_input = input("Digite quantos meses para trás deseja coletar? (Ex.: 2): ").strip()
                try:
                    meses = int(meses_input) if meses_input else 1
                except ValueError:
                    print("Valor inválido! Usando 1 mês.")
                    meses = 1
                kwargs = {"tipo_filtro": tipo_filtro, "meses": meses}
            elif tipo_filtro_opcao == "2":
                tipo_filtro = "dias"
                dias_input = input("Digite quantos dias deseja coletar? (Ex.: 15, 30, 7): ").strip()
                try:
                    dias = int(dias_input) if dias_input else 30
                except ValueError:
                    print("Valor inválido! Usando 30 dias.")
                    dias = 30
                kwargs = {"tipo_filtro": tipo_filtro, "dias": dias}
            else:
                print("Opção inválida! Usando filtro por mês com 1 mês.")
                kwargs = {"tipo_filtro": "mes", "meses": 1}

            if grupo:
                kwargs["nome_grupo"] = grupo
            if comunidade:
                kwargs["nome_comunidade"] = comunidade

            print("\nIniciando extrator...")
            try:
                extrair_dados_comunidade(**kwargs)
            except Exception as e:
                print(f"Erro ao executar o extrator: {e}")
        elif opcao == "4":
            exportar_db_para_csv()
        elif opcao == "5":
            print("\nSaindo... Até mais!")
            sys.exit(0)
        else:
            print("\nOpção inválida! Tente novamente.")


def iniciar_servidor_web(host: str = "127.0.0.1", port: int = 8000, abrir_browser: bool = False):
    setup_environment()
    print("\n" + "=" * 60, flush=True)
    print("      WHATSAPP INSIGHTS - PAINEL WEB DE CONTROLE", flush=True)
    print("=" * 60, flush=True)
    print(f"-> IP / Porta:          http://{host}:{port}", flush=True)
    print(f"-> Acesso no Navegador: http://localhost:{port}", flush=True)
    print("=" * 60, flush=True)
    print("Pressione Ctrl+C para encerrar o servidor.\n", flush=True)

    if abrir_browser or getattr(sys, "frozen", False):
        def _abrir():
            time.sleep(1.0)
            try:
                webbrowser.open(f"http://localhost:{port}")
            except Exception:
                pass
        threading.Thread(target=_abrir, daemon=True).start()

    start_server(host=host, port=port)


def main():
    setup_environment()
    parser = argparse.ArgumentParser(description="WhatsApp Insights LLM Orchestrator")
    parser.add_argument("--cli", action="store_true", help="Executa no modo terminal interativo CLI")
    parser.add_argument("--host", default="127.0.0.1", help="Host IP para o servidor web (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Porta para o servidor web (default: 8000)")
    parser.add_argument("--open", action="store_true", help="Abre o navegador automaticamente")

    args = parser.parse_args()

    if args.cli:
        menu_cli()
    else:
        iniciar_servidor_web(host=args.host, port=args.port, abrir_browser=args.open)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nOperação cancelada pelo usuário. Saindo...")
        sys.exit(0)