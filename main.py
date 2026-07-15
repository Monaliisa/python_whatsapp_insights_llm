import sys
from services.coletor import iniciar_coletor
from services.extrator import extrair_dados_comunidade

def menu():
    while True:
        print("\n" + "="*40)
        print("    ORQUESTRADOR DE AUTOMAÇÃO WHATSAPP")
        print("="*40)
        print("1. Iniciar Coletor (Fazer Login / Escanear QR Code)")
        print("2. Iniciar Extrator (Coletar mensagens do Grupo)")
        print("3. Sair")
        print("="*40)
        
        opcao = input("Escolha uma opção (1-3): ").strip()
        
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
            
            kwargs = {}
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
