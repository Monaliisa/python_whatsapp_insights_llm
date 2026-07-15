import os
import time
from playwright.sync_api import sync_playwright

# INDIQUE AQUI OS NOMES EXATOS
NOME_DA_COMUNIDADE = "Nome da Comunidade Principal"
NOME_DO_GRUPO = "Nome do Grupo de Teste"

def extrair_dados_comunidade():
    caminho_sessao = os.path.join(os.getcwd(), "sessao_whatsapp")
    
    with sync_playwright() as p:
        print("Abrindo navegador...")
        contexto = p.chromium.launch_persistent_context(
            user_data_dir=caminho_sessao,
            headless=False,
            args=["--start-maximized"],
            no_viewport=True
        )
        
        pagina = contexto.pages[0]
        pagina.goto("https://web.whatsapp.com")
        
        print("Aguardando carregamento do WhatsApp Web...")
        seletor_busca = "div[contenteditable='true'][data-tab='3']"
        pagina.wait_for_selector(seletor_busca, timeout=60000)
        
        # Estratégia: Buscar o grupo específico diretamente pela barra de pesquisa
        print(f"Buscando por: '{NOME_DO_GRUPO}'...")
        pagina.click(seletor_busca)
        
        # Limpa o campo de busca antes de digitar (caso haja algo)
        pagina.keyboard.press("Control+A")
        pagina.keyboard.press("Backspace")
        
        pagina.fill(seletor_busca, NOME_DO_GRUPO)
        time.sleep(2) # Pausa um pouco maior para o WhatsApp expandir a comunidade na lateral
        
        # O WhatsApp Web usa spans para os títulos dos chats na lista lateral
        # Vamos tentar clicar no texto exato do grupo que apareceu na busca
        seletor_item_lista = f"span[title='{NOME_DO_GRUPO}']"
        
        try:
            pagina.wait_for_selector(seletor_item_lista, timeout=15000)
            pagina.click(seletor_item_lista)
            print(f"Sucesso: Grupo '{NOME_DO_GRUPO}' aberto!")
        except Exception:
            print(f"Não encontrei o grupo direto. Tentando buscar pela comunidade '{NOME_DA_COMUNIDADE}'...")
            # Se não achou o grupo direto, busca a comunidade principal para abrir a árvore de grupos
            pagina.click(seletor_busca)
            pagina.keyboard.press("Control+A")
            pagina.keyboard.press("Backspace")
            pagina.fill(seletor_busca, NOME_DA_COMUNIDADE)
            time.sleep(2)
            
            seletor_comunidade = f"span[title='{NOME_DA_COMUNIDADE}']"
            pagina.wait_for_selector(seletor_comunidade, timeout=10000)
            pagina.click(seletor_comunidade)
            
            # Agora que a comunidade abriu, tentamos clicar no grupo dentro dela
            time.sleep(1)
            pagina.click(seletor_item_lista)
            print(f"Sucesso: Grupo aberto através da Comunidade!")

        print("Aguardando o carregamento das mensagens...")
        seletor_chat = "div[data-tab='8']"
        pagina.wait_for_selector(seletor_chat, timeout=20000)
        time.sleep(2)
        
        print("\n--- Coletando as últimas mensagens ---")
        baloes_mensagens = pagina.query_selector_all("div.message-in, div.message-out")
        
        for i, balao in enumerate(baloes_mensagens[-10:]):
            elemento_texto = balao.query_selector("span.copyable-text")
            if elemento_texto:
                texto = elemento_texto.inner_text()
                print(f"\n[Mensagem {i+1}]: {texto}")
                print("-" * 30)
                
        print("\nFechando em 5 segundos...")
        time.sleep(5)
        contexto.close()

if __name__ == "__main__":
    extrair_dados_comunidade()