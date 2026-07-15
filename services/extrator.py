import os
import time
from playwright.sync_api import sync_playwright

# CONFIGURAÇÕES PADRÃO (Podem ser editadas aqui ou passadas como argumento)
NOME_DA_COMUNIDADE = ""
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

        print("\n--- Coletando as últimas mensagens ---")
        
        # 1. Tentamos pegar as mensagens usando o atributo de papel de mensagem do WhatsApp
        baloes_mensagens = pagina.query_selector_all("[role='row'], div.message-in, div.message-out")
        
        # 2. Se falhar, tentamos pegar pelas caixas que contêm textos selecionáveis
        if len(baloes_mensagens) == 0:
            baloes_mensagens = pagina.query_selector_all("div.copyable-text")
            
        print(f"Quantidade de balões encontrados na tela: {len(baloes_mensagens)}")
        
        mensagens_encontradas = 0
        
        for i, balao in enumerate(baloes_mensagens[-15:]): # Olhamos até as últimas 15 mensagens
            texto = None
            
            # Tentativa A: Buscar pelo texto selecionável padrão do WhatsApp Web
            elemento_texto = balao.query_selector("span.selectable-text")
            
            if not elemento_texto:
                # Tentativa B: Buscar por qualquer span de texto copiável
                elemento_texto = balao.query_selector(".copyable-text")
                
            if elemento_texto:
                texto = elemento_texto.inner_text()
            else:
                # Tentativa C: Pegar o texto limpo direto do bloco se não achar os seletores acima
                texto_bruto = balao.inner_text()
                if texto_bruto and len(texto_bruto.strip()) > 2:
                    # Limpa quebras de linha e pega a primeira parte (geralmente a mensagem)
                    linhas = [l.strip() for l in texto_bruto.split('\n') if l.strip()]
                    if linhas and not linhas[0].replace(':', '').isdigit(): # Ignora se for só horário
                        texto = linhas[0]
            
            if texto:
                mensagens_encontradas += 1
                print(f"\n[Mensagem {mensagens_encontradas}]: {texto}")
                print("-" * 30)
                
        if mensagens_encontradas == 0:
            print("\n[Aviso]: Não consegui extrair o texto de nenhuma mensagem.")
            print("Certifique-se de que enviou mensagens de texto de verdade no grupo pelo celular recentemente!")
