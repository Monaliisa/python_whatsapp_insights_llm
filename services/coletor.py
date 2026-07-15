import os
import time
import sys
from playwright.sync_api import sync_playwright

def iniciar_coletor():
    caminho_projeto = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    caminho_sessao = os.path.join(caminho_projeto, "sessao_whatsapp")
    
    with sync_playwright() as p:
        print("Iniciando o navegador...")
        
        contexto = p.chromium.launch_persistent_context(
            user_data_dir=caminho_sessao,
            headless=False,
            args=["--start-maximized"]
        )
        
        pagina = contexto.pages[0]
        
        print("Acessando o WhatsApp Web...")
        pagina.goto("https://web.whatsapp.com")
        
        print("\n[AVISO] Se for a primeira vez, escaneie o QR Code na tela.")
        print("Aguardando a sincronização das conversas...\n")
        
        try:
            # Aguarda o painel de conversas ou a barra de pesquisa carregar (mais robusto)
            seletores_login = "#pane-side, div[contenteditable='true'][data-tab='3'], div[contenteditable='true'][role='textbox']"
            pagina.wait_for_selector(seletores_login, timeout=180000)
            print("Conectado com sucesso ao WhatsApp Web!")
        except Exception as e:
            print(f"Tempo limite esgotado ou erro: {e}")
            contexto.close()
            return

        print("Navegador pronto para testes. O script vai fechar em 10 segundos...")
        time.sleep(10)
        
        contexto.close()
        print("Sessão fechada e salva localmente.")

if __name__ == "__main__":
    iniciar_coletor()
