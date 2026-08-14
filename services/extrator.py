import os
import time
import re
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright
from services.storage import init_db, save_messages

NOME_DA_COMUNIDADE = ""
NOME_DO_GRUPO = "Ciência de Dados | Comunidade Alura"


def detectar_anexos(balao):
    anexos = []

    # Imagens / thumbnails
    imgs = balao.query_selector_all("img, [data-testid*='image'], div[data-testid='image-thumb']")
    for img in imgs:
        src = img.get_attribute("src") or img.get_attribute("data-src")
        anexos.append({"type": "image", "src": src})

    # Vídeos
    videos = balao.query_selector_all("video, [data-testid*='video']")
    for v in videos:
        src = v.get_attribute("src")
        anexos.append({"type": "video", "src": src})

    # Áudio / voice notes
    audios = balao.query_selector_all("audio, [data-testid*='audio'], div[data-testid*='voice']")
    for a in audios:
        src = a.get_attribute("src")
        anexos.append({"type": "audio", "src": src})

    # Documentos (heurística por download/title/extensões)
    docs = balao.query_selector_all("a[download], [data-testid*='document'], span[title$='.pdf'], span[title$='.docx'], span[title$='.pptx']")
    for d in docs:
        filename = d.get_attribute("download") or d.get_attribute("title") or (d.inner_text() if hasattr(d, "inner_text") else None)
        anexos.append({"type": "document", "filename": filename})

    # Stickers
    stickers = balao.query_selector_all("img[alt*='sticker'], [data-testid='sticker']")
    for s in stickers:
        src = s.get_attribute("src")
        anexos.append({"type": "sticker", "src": src})

    # Enquetes / polls — heurística por aria-label/texto
    poll = balao.query_selector("div[aria-label*='enquete'], div[aria-label*='poll']")
    if not poll:
        poll = balao.query_selector("text=Enquete") or balao.query_selector("text=Poll") or balao.query_selector("text=Votar")
    if poll:
        try:
            corpo = poll.inner_text().strip()
        except Exception:
            corpo = None
        anexos.append({"type": "poll", "raw": corpo})

    return anexos

def extrair_dados_balao(balao):
    """
    Extrai metadados, identificação de reply e texto principal do balão.
    Retorna um dicionário estruturado ou None caso o balão seja inválido/sistema.
    """
    # 1. Obtenção do ID Único do WhatsApp Web para Deduplicação
    msg_id = balao.get_attribute("data-id")
    
    # 2. Extração de Metadados (Data, Hora e Remetente) via data-pre-plain-text
    # Procura no próprio balão ou em filhos (div.copyable-text)
    pre_plain_elem = balao.query_selector("[data-pre-plain-text]")
    if not pre_plain_elem:
        return None
        
    attr = pre_plain_elem.get_attribute("data-pre-plain-text") or ""
    # Padrão retornado pelo WA: "[14:20, 12/08/2026] Nome do Remetente: "
    match_meta = re.search(r"\[(\d{2}:\d{2}),\s*(\d{2}/\d{2}/\d{4})\]\s*(.*?):", attr)
    if not match_meta:
        return None
        
    hora_str, data_str, remetente = match_meta.groups()
    data_hora = datetime.strptime(f"{data_str} {hora_str}", "%d/%m/%Y %H:%M")

    # 3. Identificação e Isolação de Reply (Citação)
    is_reply = False
    quoted_author = None
    quoted_text = None
    
    # Seletores resilientes para o container de citação dentro do balão
    reply_selectors = [
        'div[role="button"][aria-label*="quoted"]',
        'div[role="button"][aria-label*="citad"]',
        'div[data-js-quoted-mention="true"]',
        'div._ak8j'
    ]
    
    reply_container = None
    for selector in reply_selectors:
        reply_container = balao.query_selector(selector)
        if reply_container:
            is_reply = True
            break
            
    if is_reply and reply_container:
        try:
            linhas_quote = [l.strip() for l in reply_container.inner_text().split("\n") if l.strip()]
            if len(linhas_quote) >= 2:
                quoted_author = linhas_quote[0]
                quoted_text = "\n".join(linhas_quote[1:])
            elif len(linhas_quote) == 1:
                quoted_text = linhas_quote[0]
        except Exception:
            pass

    # 4. Extração do Texto Principal (Resposta do Usuário)
    # Tenta seletores diretos de corpo de texto
    text_selectors = [
        "span.selectable-text.copyable-text",
        "span._ao3e",
        "span.selectable-text"
    ]
    
    texto_principal = ""
    for t_selector in text_selectors:
        # Garante que não pegamos o span de texto de dentro da citação
        elems = balao.query_selector_all(t_selector)
        for elem in elems:
            # Se o elemento não está dentro do reply_container, é o texto real
            if is_reply and reply_container:
                is_inside_reply = elem.evaluate("(el, reply) => reply.contains(el)", reply_container)
                if is_inside_reply:
                    continue
            
            txt = elem.inner_text().strip()
            if txt:
                texto_principal = txt
                break
        if texto_principal:
            break

    # Fallback: Se não encontrou por seletor, usa inner_text total limpando o trecho do reply
    if not texto_principal:
        texto_bruto = balao.inner_text()
        if is_reply and reply_container:
            texto_bruto = texto_bruto.replace(reply_container.inner_text(), "")
            
        linhas = [l.strip() for l in texto_bruto.split('\n') if l.strip()]
        linhas_validas = [
            l for l in linhas 
            if not re.match(r'^\d{2}:\d{2}$', l) 
            and l.lower() not in ['editada', 'edited', 'visto']
            and l != remetente
        ]
        if linhas_validas:
            texto_principal = "\n".join(linhas_validas)

    # Chave de identificação caso data-id falhe
    final_id = msg_id if msg_id else f"{data_hora.timestamp()}_{remetente}_{texto_principal[:20]}"

    attachments = detectar_anexos(balao)
    # e inclua nos dados retornados (não-destrutivo):
    return {
        "id": final_id,
        "data_hora": data_hora,
        "data_hora_str": data_hora.strftime('%d/%m/%Y %H:%M'),
        "remetente": remetente,
        "texto": texto_principal,
        "is_reply": is_reply,
        "reply_data": {
            "autor_citado": quoted_author,
            "texto_citado": quoted_text
        } if is_reply else None,
        "attachments": attachments,
        "has_attachments": len(attachments) > 0
    }


def extrair_dados_comunidade(nome_grupo=NOME_DO_GRUPO, nome_comunidade=NOME_DA_COMUNIDADE, semanas=1):
    caminho_projeto = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    caminho_sessao = os.path.join(caminho_projeto, "sessao_whatsapp")
    
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
        pagina.wait_for_selector("#pane-side, #app", timeout=90000)
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

        print("\n--- Iniciando rolagem incremental e raspagem contínua ---")
        
        # Estrutura de armazenamento com deduplicação nativa por ID
        mensagens_coletadas = {}
        
        atingiu_limite = False
        tentativas_sem_novos_dados = 0
        seletor_baloes = "#main div[data-id], #main div.message-in, #main div.message-out"

        while not atingiu_limite and tentativas_sem_novos_dados < 12:
            baloes_visiveis = pagina.query_selector_all(seletor_baloes)
            total_antes = len(mensagens_coletadas)
            
            # Raspagem imediata dos balões presentes no DOM atual
            for balao in baloes_visiveis:
                dados = extrair_dados_balao(balao)
                if not dados:
                    continue
                
                # Interrupção temporal: verifica se atingiu mensagens anteriores à janela
                if dados["data_hora"] < data_limite:
                    print(f"\n[Alerta] Alcançou mensagem de {dados['data_hora_str']} (Anterior a {data_limite.strftime('%d/%m/%Y')}). Encerrando scroll...")
                    atingiu_limite = True
                    break
                
                # Adiciona ao dicionário (se já existir, atualiza sem duplicar)
                mensagens_coletadas[dados["id"]] = dados

            # Verifica progresso de novas mensagens coletadas
            if len(mensagens_coletadas) == total_antes:
                tentativas_sem_novos_dados += 1
            else:
                tentativas_sem_novos_dados = 0

            # Executa a rolagem para cima
            pagina.evaluate("""
                let container = document.querySelector("#main div[tabindex='-1']") || 
                                document.querySelector("div[data-tab='8']") ||
                                document.querySelector("#main .copyable-area > div");
                if (container) {
                    container.scrollTop = 0;
                }
            """)
            pagina.keyboard.press("PageUp")
            time.sleep(1.8)  # Tempo para renderização e requisição de histórico

        # Ordenação cronológica das mensagens extraídas
        lista_final = sorted(mensagens_coletadas.values(), key=lambda x: x["data_hora"])

        print(f"\nTotal de mensagens extraídas com sucesso: {len(lista_final)}")
        print("="*60)
        
        for i, m in enumerate(lista_final, 1):
            status_reply = "[REPLY]" if m.get("is_reply") else "[MENSAGEM]"
            attachment_flag = " [ANEXO]" if m.get("has_attachments") else ""
            print(f"[{i}] {status_reply}{attachment_flag} [{m['data_hora_str']}] {m['remetente']}: {m['texto']}")
            if m.get("is_reply") and m.get("reply_data"):
                print(f"   └──> Em resposta a {m['reply_data']['autor_citado']}: \"{m['reply_data']['texto_citado']}\"")

            if m.get("has_attachments"):
                anexos = m.get("attachments", [])
                tipos = sorted({a.get("type") for a in anexos if a.get("type")})
                # Mapear tipos técnicos para rótulos em português
                label_map = {
                    "image": "imagem",
                    "video": "vídeo",
                    "audio": "áudio",
                    "document": "documento",
                    "sticker": "figurinha",
                    "poll": "enquete"
                }
                tipos_label = [label_map.get(t, t) for t in tipos]
                if tipos_label:
                    print(f"   └──> Contém: {', '.join(tipos_label)}")

            print("-" * 50)

        # Persistir mensagens no banco local (SQLite)
        try:
            init_db()
            save_messages(lista_final)
            print("[INFO] Mensagens salvas em data/messages.db")
        except Exception as e:
            print(f"[ERRO] Falha ao salvar mensagens: {e}")

        time.sleep(3)
        contexto.close()

if __name__ == "__main__":
    extrair_dados_comunidade()