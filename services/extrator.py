import os
import time
from playwright.sync_api import sync_playwright

# CONFIGURAÇÕES PADRÃO (Podem ser editadas aqui ou passadas como argumento)
NOME_DA_COMUNIDADE = "Nome da Comunidade Principal"
NOME_DO_GRUPO = "Nome do Grupo de Teste"

def extrair_dados_comunidade(nome_grupo=NOME_DO_GRUPO, nome_comunidade=NOME_DA_COMUNIDADE):
    caminho_projeto = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    caminho_sessao = os.path.join(caminho_projeto, "sessao_whatsapp")
    
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
        # Suporta múltiplos seletores comuns para a barra de pesquisa
        seletor_busca = "div[contenteditable='true'][data-tab='3'], div[contenteditable='true'][role='textbox'], [data-testid='chat-list-search']"
        pagina.wait_for_selector(seletor_busca, timeout=60000)
        
        # Estratégia: Buscar o grupo específico diretamente pela barra de pesquisa
        print(f"Buscando por: '{nome_grupo}'...")
        pagina.click(seletor_busca)
        
        # Limpa o campo de busca antes de digitar (caso haja algo)
        pagina.keyboard.press("Control+A")
        pagina.keyboard.press("Backspace")
        
        pagina.fill(seletor_busca, nome_grupo)
        time.sleep(2) # Pausa um pouco maior para o WhatsApp expandir a comunidade na lateral
        
        # O WhatsApp Web usa spans para os títulos dos chats na lista lateral
        # Vamos tentar clicar no texto exato do grupo que apareceu na busca
        seletor_item_lista = f"span[title='{nome_grupo}']"
        
        try:
            pagina.wait_for_selector(seletor_item_lista, timeout=15000)
            pagina.click(seletor_item_lista)
            print(f"Sucesso: Grupo '{nome_grupo}' aberto!")
        except Exception:
            print(f"Não encontrei o grupo direto. Tentando buscar pela comunidade '{nome_comunidade}'...")
            # Se não achou o grupo direto, busca a comunidade principal para abrir a árvore de grupos
            pagina.click(seletor_busca)
            pagina.keyboard.press("Control+A")
            pagina.keyboard.press("Backspace")
            pagina.fill(seletor_busca, nome_comunidade)
            time.sleep(2)
            
            seletor_comunidade = f"span[title='{nome_comunidade}']"
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
