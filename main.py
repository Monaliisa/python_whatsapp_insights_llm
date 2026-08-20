import os
import sys
from services.coletor import iniciar_coletor
from services.extrator import extrair_dados_comunidade
from services.storage import export_to_csv, get_db_path


def exportar_db_para_csv():
    caminho_db = get_db_path()

    if not os.path.exists(caminho_db):
        print(f"\nBanco não encontrado em: {caminho_db}")
        return

    nome_arquivo = input("Digite o nome do arquivo CSV (ou Enter para usar export_messages.csv): ").strip() or "export_messages.csv"
    destino = nome_arquivo

    if not os.path.isabs(destino):
        pasta_data = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
        os.makedirs(pasta_data, exist_ok=True)
        destino = os.path.join(pasta_data, destino)

    try:
        export_to_csv(destino, db_path=caminho_db)
        print(f"\nCSV exportado com sucesso em: {destino}")
    except Exception as e:
        print(f"\nErro ao exportar CSV: {e}")


def menu():
    while True:
        print("\n" + "="*40)
        print("    ORQUESTRADOR DE AUTOMAÇÃO WHATSAPP")
        print("="*40)
        print("1. Iniciar Coletor (Fazer Login / Escanear QR Code)")
        print("2. Iniciar Extrator (Coletar mensagens do Grupo)")
        print("3. Exportar banco para CSV")
        print("4. Sair")
        print("="*40)

        opcao = input("Escolha uma opção (1-4): ").strip()

        if opcao == "1":
            print("\nIniciando coletor...")
            try:
                iniciar_coletor()
            except Exception as e:
                print(f"Erro ao executar o coletor: {e}")
        elif opcao == "2":
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
                kwargs = {'tipo_filtro': tipo_filtro, 'meses': meses}
            elif tipo_filtro_opcao == "2":
                tipo_filtro = "dias"
                dias_input = input("Digite quantos dias deseja coletar? (Ex.: 15, 30, 7): ").strip()
                try:
                    dias = int(dias_input) if dias_input else 30
                except ValueError:
                    print("Valor inválido! Usando 30 dias.")
                    dias = 30
                kwargs = {'tipo_filtro': tipo_filtro, 'dias': dias}
            else:
                print("Opção inválida! Usando filtro por mês com 1 mês.")
                kwargs = {'tipo_filtro': 'mes', 'meses': 1}

            if grupo:
                kwargs['nome_grupo'] = grupo
            if comunidade:
                kwargs['nome_comunidade'] = comunidade

            print("\nIniciando extrator...")
            try:
                extrair_dados_comunidade(**kwargs)
            except Exception as e:
                print(f"Erro ao executar o extrator: {e}")
        elif opcao == "3":
            exportar_db_para_csv()
        elif opcao == "4":
            print("\nSaindo... Até mais!")
            sys.exit(0)
        else:
            print("\nOpção inválida! Tente novamente.")


if __name__ == "__main__":
    try:
        menu()
    except KeyboardInterrupt:
        print("\n\nOperação cancelada pelo usuário. Saindo...")
        sys.exit(0)

        