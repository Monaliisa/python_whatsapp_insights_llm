import os
import time
import re
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright

NOME_DA_COMUNIDADE = ""
NOME_DO_GRUPO = "Ciência de Dados | Comunidade Alura"

def extrair_data_mensagem(balao):
    """Extrai a data e hora do atributo data-pre-plain-text ou tenta ler o texto visual."""
    elemento = balao.query_selector("div.copyable-text")
    if elemento:
        attr = elemento.get_attribute("data-pre-plain-text")
        if attr:
            match = re.search(r"\[(\d{2}:\d{2}),\s*(\d{2}/\d{2}/\d{4})\]", attr)
            if match:
                hora_str, data_str = match.groups()
                return datetime.strptime(f"{data_str} {hora_str}", "%d/%m/%Y %H:%M")
    return None

def extrair_texto_balao(balao):
    """Tenta extrair o texto do balão através de múltiplos seletores resilientes."""
    seletores_texto = [
        "span.selectable-text.copyable-text",
        "span.selectable-text",
        "div._akbu",  # Classe comum nas versões recentes
        "span._ao3e",  # Classe de corpo de texto
        ".copyable-text"
    ]
    
    for seletor in seletores_texto:
        elem = balao.query_selector(seletor)
        if elem:
            txt = elem.inner_text().strip()
            if txt:
                return txt

    texto_bruto = balao.inner_text()
    if texto_bruto:
        linhas = [l.strip() for l in texto_bruto.split('\n') if l.strip()]
        linhas_validas = [
            l for l in linhas 
            if not re.match(r'^\d{2}:\d{2}$', l) 
            and l.lower() not in ['editada', 'edited', 'visto']
        ]
        if linhas_validas:
            return "\n".join(linhas_validas)
            
    return None

def extrair_dados_comunidade(nome_grupo=NOME_DO_GRUPO, nome_comunidade=NOME_DA_COMUNIDADE, semanas=1):
    caminho_projeto = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    caminho_sessao = os.path.join(caminho_projeto, "sessao_whatsapp")
    
    # Define a data limite considerando o início do dia (00:00:00)
    agora = datetime.now()
    data_limite = (agora - timedelta(weeks=semanas)).replace(hour=0, minute=0, second=0, microsecond=0)
    print(f"\n[Filtro] Coletando mensagens de {data_limite.strftime('%d/%m/%Y')} até hoje ({agora.strftime('%d/%m/%Y')})")

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
        seletor_painel_lateral = "#pane-side, #app"
        pagina.wait_for_selector(seletor_painel_lateral, timeout=90000)
        time.sleep(3)
        
        print(f"Buscando por: '{nome_grupo}'...")
        pagina.keyboard.press("Control+f")
        time.sleep(1)
        pagina.keyboard.type(nome_grupo)
        time.sleep(2)
        
        seletor_item_lista = f"span[title='{nome_grupo}']"
        
        try:
            pagina.wait_for_selector(seletor_item_lista, timeout=15000)
            pagina.click(seletor_item_lista)
            print(f"Sucesso: Grupo '{nome_grupo}' aberto!")
        except Exception:
            print(f"Grupo não encontrado direto. Buscando pela comunidade '{nome_comunidade}'...")
            pagina.keyboard.press("Control+f")
            time.sleep(1)
            pagina.keyboard.press("Control+A")
            pagina.keyboard.press("Backspace")
            pagina.keyboard.type(nome_comunidade)
            time.sleep(2)
            
            seletor_comunidade = f"span[title='{nome_comunidade}']"
            pagina.wait_for_selector(seletor_comunidade, timeout=10000)
            pagina.click(seletor_comunidade)
            time.sleep(1)
            pagina.click(seletor_item_lista)

        print("\nAguardando o painel de mensagens carregar...")
        pagina.wait_for_selector("#main", timeout=20000)
        time.sleep(2)

        pagina.click("#main")

        print("\n--- Iniciando rolagem para carregar histórico ---")
        
        atingiu_limite = False
        tentativas_sem_novas_msg = 0
        ultimo_total_baloes = 0

        seletor_baloes = "#main div[role='row'], #main div.message-in, #main div.message-out"

        while not atingiu_limite and tentativas_sem_novas_msg < 10:
            baloes = pagina.query_selector_all(seletor_baloes)
            
            if len(baloes) == ultimo_total_baloes:
                tentativas_sem_novas_msg += 1
            else:
                tentativas_sem_novas_msg = 0
                ultimo_total_baloes = len(baloes)

            # Inspeciona os 5 balões superiores para verificar se a data limite foi atingida
            if baloes:
                for balao in baloes[:5]:
                    dt = extrair_data_mensagem(balao)
                    if dt and dt < data_limite:
                        print(f"Alcançou mensagem anterior a {data_limite.strftime('%d/%m/%Y')}. Finalizando rolagem...")
                        atingiu_limite = True
                        break

            pagina.evaluate("""
                let container = document.querySelector("#main div[tabindex='-1']") || 
                                document.querySelector("div[data-tab='8']") ||
                                document.querySelector("#main .copyable-area > div");
                if (container) {
                    container.scrollTop = 0;
                }
            """)
            pagina.keyboard.press("PageUp")
            time.sleep(2.5)

        print("\n--- Processando e filtrando mensagens ---")
        baloes_finais = pagina.query_selector_all(seletor_baloes)
        
        mensagens_filtradas = []
        for balao in baloes_finais:
            dt_msg = extrair_data_mensagem(balao)
            texto = extrair_texto_balao(balao)

            if texto:
                if dt_msg:
                    if dt_msg >= data_limite:
                        mensagens_filtradas.append((dt_msg.strftime('%d/%m/%Y %H:%M'), texto))
                else:
                    mensagens_filtradas.append(("Data/Hora N/D", texto))

        print(f"\nTotal de mensagens encontradas na janela de {semanas} semana(s): {len(mensagens_filtradas)}")
        print("="*50)
        
        for i, (data_hora, msg) in enumerate(mensagens_filtradas, 1):
            print(f"[{i}] [{data_hora}]: {msg}")
            print("-" * 40)

        print("\nFinalizado. Fechando em 5 segundos...")
        time.sleep(5)
        contexto.close()

if __name__ == "__main__":
    extrair_dados_comunidade()