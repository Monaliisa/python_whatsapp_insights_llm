import os
import time
import re
import uuid
import unicodedata
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from services.paths import get_session_dir
from services.storage import (
    count_messages,
    gerar_grupo_id,
    init_db,
    save_messages,
    set_active_group,
)

NOME_DA_COMUNIDADE = ""
NOME_DO_GRUPO = ""


def normalizar_texto_busca(texto: str) -> str:
    """
    Remove acentos, caracteres especiais, pipes e normaliza espaços para comparação tolerante.
    Exemplo: 'Ciência de Dados | Comunidade Alura' -> 'ciencia de dados comunidade alura'
    """
    if not texto:
        return ""
    texto_sem_acento = unicodedata.normalize("NFKD", texto).encode("ASCII", "ignore").decode("ASCII")
    texto_limpo = re.sub(r"[^\w\s]", " ", texto_sem_acento.lower())
    return " ".join(texto_limpo.split())


def abrir_chat_por_nome(pagina, nome, nome_alternativo=None):
    """
    Localiza e abre um chat no WhatsApp Web utilizando a caixa de pesquisa interna
    e comparação inteligente de strings (tolerante a acentuação, pipes e variações).
    """
    candidatos_brutos = []
    
    # Se o usuário passou "Grupo | Comunidade", a prioridade número 1 é o nome real do grupo (antes do pipe)
    for valor in [nome]:
        if not valor or not valor.strip():
            continue
        v = valor.strip()
        if "|" in v:
            partes = [p.strip() for p in v.split("|") if p.strip()]
            if partes:
                # O nome do grupo geralmente é a primeira parte antes do pipe
                candidatos_brutos.append(partes[0])
                candidatos_brutos.append(v)
                candidatos_brutos.append(" ".join(partes))
        else:
            candidatos_brutos.append(v)
            if "-" in v:
                partes = [p.strip() for p in v.split("-") if p.strip()]
                if partes:
                    candidatos_brutos.append(partes[0])
                    candidatos_brutos.append(v)

    # Remove duplicatas preservando a ordem
    candidatos = list(dict.fromkeys(candidatos_brutos))
    print(f"Candidatos para busca de grupo: {candidatos}")

    # 1. Verifica se o chat desejado já está aberto no painel principal (#main)
    try:
        header_title = pagina.locator("#main header span[title], #main header div[title]").first
        if header_title.count() > 0:
            current_chat = header_title.get_attribute("title") or header_title.inner_text() or ""
            current_norm = normalizar_texto_busca(current_chat)
            for c in candidatos:
                c_norm = normalizar_texto_busca(c)
                if c_norm and (c_norm == current_norm or c_norm in current_norm or current_norm in c_norm):
                    print(f"Chat já está atualmente aberto no painel: '{current_chat}'")
                    return True
    except Exception:
        pass

    seletores_search_box = [
        "div[contenteditable='true'][data-tab='3']",
        "#side div[role='textbox']",
        "div[role='textbox'][aria-label*='Pesquisar']",
        "div[role='textbox'][aria-label*='Search']",
        "div[role='textbox'][title*='Pesquisar']",
        "div[role='textbox']",
        "button[aria-label*='Pesquisar']",
        "button[aria-label*='Search']",
    ]

    for termo in candidatos:
        termo_norm = normalizar_texto_busca(termo)
        if not termo_norm:
            continue

        print(f"Pesquisando grupo via campo de busca: '{termo}'...")

        # 2. Localiza e foca a caixa de pesquisa do WhatsApp Web
        search_elem = None
        for sel in seletores_search_box:
            try:
                elem = pagina.locator(sel).first
                if elem.count() > 0 and elem.is_visible():
                    search_elem = elem
                    break
            except Exception:
                pass

        if search_elem:
            try:
                search_elem.click()
                time.sleep(0.4)
                # Limpa qualquer busca anterior
                pagina.keyboard.press("Control+A")
                pagina.keyboard.press("Backspace")
                time.sleep(0.2)
                # No WhatsApp Web (div contenteditable), keyboard.type é obrigatório para disparar eventos do React
                pagina.keyboard.type(termo, delay=40)
                time.sleep(2.0)  # Aguarda o WhatsApp filtrar e renderizar os resultados
            except Exception as e:
                print(f"Aviso: falha ao interagir com search_elem: {e}")
                try:
                    pagina.keyboard.type(termo, delay=40)
                    time.sleep(2.0)
                except Exception:
                    pass

        # 3. Varre os itens de resultado de busca e painel lateral
        seletores_itens = [
            "#pane-side span[title]",
            "div[role='gridcell'] span[title]",
            "div[role='listitem'] span[title]",
            "#pane-side div[role='gridcell']",
            "#pane-side div[role='listitem']",
        ]

        encontrou = False
        for sel in seletores_itens:
            try:
                locators = pagina.locator(sel)
                total = locators.count()
                for i in range(total):
                    item = locators.nth(i)
                    if not item.is_visible():
                        continue
                    titulo = item.get_attribute("title") or item.inner_text() or ""
                    titulo_norm = normalizar_texto_busca(titulo)

                    if not titulo_norm:
                        continue

                    # Casamento exato ou por substring normalizada
                    if (
                        termo_norm == titulo_norm
                        or termo_norm in titulo_norm
                        or titulo_norm in termo_norm
                    ):
                        print(f"Chat correspondente encontrado: '{titulo}' (termo: '{termo}'). Clicando...")
                        try:
                            # Tenta clicar no elemento encontrado ou container ancestral
                            item.click(timeout=6000, force=True)
                        except Exception:
                            try:
                                item.locator("xpath=ancestor-or-self::div[@role='listitem' or @role='row' or @tabindex='-1'][1]").click(timeout=6000, force=True)
                            except Exception:
                                item.locator("..").click(timeout=6000)

                        # Tenta confirmar via Enter caso a caixa de busca ainda esteja ativa
                        try:
                            pagina.keyboard.press("Enter")
                        except Exception:
                            pass

                        time.sleep(2.0)

                        # Verifica se o painel #main carregou
                        try:
                            pagina.wait_for_selector("#main", timeout=12000)
                            print(f"Sucesso: conversa '{titulo}' aberta no painel principal.")
                            encontrou = True
                            break
                        except Exception:
                            pass
                if encontrou:
                    break
            except Exception:
                pass

        if encontrou:
            # NUNCA pressionar Escape aqui, pois no WhatsApp Web o Escape fecha o chat ativo!
            # Apenas damos foco no painel principal da conversa
            try:
                pagina.locator("#main").click(timeout=3000)
            except Exception:
                pass
            return True

        # Se não encontrou com este candidato, limpa a caixa de pesquisa antes de tentar o próximo termo
        try:
            pagina.keyboard.press("Escape")
            time.sleep(0.5)
        except Exception:
            pass

    # 4. Fallback: Varredura por rolagem no painel lateral
    print("Tentando fallback por rolagem direta na lista de conversas...")
    try:
        for _ in range(3):
            items = pagina.query_selector_all("#pane-side span[title]")
            for it in items:
                try:
                    title = (it.get_attribute("title") or "").strip()
                    title_norm = normalizar_texto_busca(title)
                    for c in candidatos:
                        c_norm = normalizar_texto_busca(c)
                        if c_norm and (c_norm == title_norm or c_norm in title_norm or title_norm in c_norm):
                            print(f"Chat localizado no fallback: '{title}'. Clicando...")
                            it.click()
                            time.sleep(2.0)
                            pagina.wait_for_selector("#main", timeout=10000)
                            return True
                except Exception:
                    pass
            # Rola um pouco a lista lateral para carregar mais itens
            pagina.evaluate("""
                let pane = document.querySelector("#pane-side");
                if (pane) pane.scrollTop += 500;
            """)
            time.sleep(1.0)
    except Exception as e:
        print(f"Erro durante o fallback de rolagem: {e}")

    print("Nenhum chat localizado para os candidatos fornecidos.")
    return False


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
    
    # Seletores resilientes para o container de citação dentro do balão (com flag case-insensitive)
    reply_selectors = [
        'div[role="button"][aria-label*="quoted" i]',
        'div[role="button"][aria-label*="citad" i]',
        'div[aria-label*="quoted" i]',
        'div[aria-label*="citad" i]',
        'div[data-testid="quoted-message"]',
        'div[data-testid*="quoted" i]',
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
            # 1. Tenta extrair via aria-label acessível do container
            aria_label = reply_container.get_attribute("aria-label") or ""
            if aria_label:
                m_aria = re.search(
                    r"(?:Mensagem citada|Quoted message)(?:\s+de|\s+from)?\s*(.*?):\s*(.*)",
                    aria_label,
                    re.IGNORECASE,
                )
                if m_aria:
                    quoted_author = m_aria.group(1).strip()
                    quoted_text = m_aria.group(2).strip()

            # 2. Se não extraiu via aria-label, decompõe o inner_text do container de citação
            if not quoted_author or not quoted_text:
                linhas_quote = [l.strip() for l in reply_container.inner_text().split("\n") if l.strip()]
                if len(linhas_quote) >= 2:
                    if not quoted_author:
                        quoted_author = linhas_quote[0]
                    if not quoted_text:
                        quoted_text = "\n".join(linhas_quote[1:])
                elif len(linhas_quote) == 1:
                    # Em citações de mídia/sem texto, a única linha é o autor citado
                    if not quoted_author:
                        quoted_author = linhas_quote[0]
                    if not quoted_text:
                        quoted_text = ""
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
        "reply_author": quoted_author if is_reply else None,
        "reply_text": quoted_text if is_reply else None,
        "reply_data": {
            "autor_citado": quoted_author,
            "texto_citado": quoted_text
        } if is_reply else None,
        "attachments": attachments,
        "has_attachments": len(attachments) > 0
    }


def calcular_data_limite(
    unidade_tempo: str = "dias",
    valor: int | float = 7,
    tipo_filtro: str | None = None,
    meses: int = 1,
    dias: int = 30,
) -> tuple[datetime, datetime, str]:
    """
    Calcula a data e hora limite com base na unidade de tempo selecionada (horas, dias, semanas, meses).
    Suporta retrocompatibilidade com tipo_filtro ('mes' ou 'dias').
    """
    agora = datetime.now()

    # Retrocompatibilidade com parâmetros legados
    if tipo_filtro:
        tf = str(tipo_filtro).lower().strip()
        if tf in ["mes", "meses"]:
            unidade_tempo = "meses"
            valor = meses
        elif tf in ["dias", "dia"]:
            unidade_tempo = "dias"
            valor = dias

    unidade = str(unidade_tempo).lower().strip()
    val = max(1, int(valor)) if valor else 1

    if unidade in ["hora", "horas", "h"]:
        data_limite = agora - timedelta(hours=val)
        label = f"última(s) {val} hora(s)"
    elif unidade in ["dia", "dias", "d"]:
        data_limite = (agora - timedelta(days=val)).replace(hour=0, minute=0, second=0, microsecond=0)
        label = f"último(s) {val} dia(s)"
    elif unidade in ["semana", "semanas", "sem", "w"]:
        data_limite = (agora - timedelta(weeks=val)).replace(hour=0, minute=0, second=0, microsecond=0)
        label = f"última(s) {val} semana(s)"
    elif unidade in ["mes", "meses", "m"]:
        primeiro_dia_do_mes = agora.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        data_limite = primeiro_dia_do_mes
        for _ in range(val):
            ano = data_limite.year
            mes = data_limite.month - 1
            if mes == 0:
                mes = 12
                ano -= 1
            data_limite = data_limite.replace(year=ano, month=mes, day=1)
        label = f"último(s) {val} mês(es)"
    else:
        data_limite = (agora - timedelta(days=val)).replace(hour=0, minute=0, second=0, microsecond=0)
        label = f"último(s) {val} dia(s)"

    return data_limite, agora, label


def extrair_dados_comunidade(
    nome_grupo: str = NOME_DO_GRUPO,
    nome_comunidade: str = NOME_DA_COMUNIDADE,
    unidade_tempo: str = "dias",
    valor: int = 7,
    tipo_filtro: str | None = None,
    meses: int = 1,
    dias: int | None = None,
):
    caminho_sessao = str(get_session_dir())

    data_limite, agora, label_filtro = calcular_data_limite(
        unidade_tempo=unidade_tempo,
        valor=valor,
        tipo_filtro=tipo_filtro,
        meses=meses,
        dias=dias or 30,
    )
    print(f"\n[Filtro] Coletando mensagens por: {label_filtro} ({data_limite.strftime('%d/%m/%Y %H:%M')} até {agora.strftime('%d/%m/%Y %H:%M')})")

    with sync_playwright() as p:
        print("Abrindo navegador...")
        contexto = p.chromium.launch_persistent_context(
            user_data_dir=caminho_sessao,
            headless=False,
            args=["--start-maximized"],
            no_viewport=True,
        )

        try:
            pagina = contexto.pages[0] if contexto.pages else contexto.new_page()
            print("Acessando https://web.whatsapp.com ...")
            pagina.goto("https://web.whatsapp.com")

            print("Aguardando carregamento e sincronização do WhatsApp Web...")
            seletores_painel = "#pane-side, div[contenteditable='true'][data-tab='3'], header[data-testid='chatlist-header']"

            # Verifica se já está conectado
            try:
                pagina.wait_for_selector(seletores_painel, timeout=15000)
                print(">> WhatsApp Web conectado com sucesso!")
            except PlaywrightTimeoutError:
                print("\n" + "-" * 55)
                print("[AGUARDANDO LOGIN] Por favor, escaneie o QR Code na tela...")
                print("O navegador permanecerá aberto aguardando a sincronização...")
                print("-" * 55 + "\n")
                try:
                    pagina.wait_for_selector(seletores_painel, timeout=300000)
                    print(">> Login concluído com sucesso!")
                except PlaywrightTimeoutError:
                    raise RuntimeError("Tempo limite de 5 minutos esgotado aguardando leitura do QR Code.")

            time.sleep(2)

            print(f"Buscando por: '{nome_grupo}'...")
            if not abrir_chat_por_nome(pagina, nome_grupo, nome_comunidade):
                print(f"ERRO: Não foi possível localizar o chat '{nome_grupo}'.")
                print("Sugestões: confira diferenças de espaços/caracteres; confira se o grupo está arquivado ou dentro de uma comunidade.")
                raise RuntimeError(f"Não foi possível localizar o chat '{nome_grupo}'.")

            print("\nAguardando o painel de mensagens carregar...")
            pagina.wait_for_selector("#main", timeout=20000)
            time.sleep(2)
            pagina.click("#main")

            print("\n--- Iniciando rolagem incremental e raspagem contínua ---")

            # Estrutura de armazenamento com deduplicação nativa por ID
            mensagens_coletadas = {}

            atingiu_limite = False
            tentativas_sem_novos_dados = 0
            seletor_baloes = "#main div[data-id], #main div.message-in, #main div.message-out, #main div[data-pre-plain-text]"

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
                        print(f"\n[Alerta] Alcançou mensagem de {dados['data_hora_str']} (Anterior a {data_limite.strftime('%d/%m/%Y %H:%M')}). Encerrando scroll...")
                        atingiu_limite = True
                        break

                    # Adiciona ao dicionário (se já existir, atualiza sem duplicar)
                    mensagens_coletadas[dados["id"]] = dados

                # Verifica progresso de novas mensagens coletadas
                if len(mensagens_coletadas) == total_antes:
                    tentativas_sem_novos_dados += 1
                else:
                    tentativas_sem_novos_dados = 0

                # Executa a rolagem para cima com múltiplos seletores e wheel
                pagina.evaluate("""
                    let container = document.querySelector("#main div[data-testid='conversation-panel-messages']") ||
                                    document.querySelector("#main div[tabindex='-1']") || 
                                    document.querySelector("div[data-tab='8']") ||
                                    document.querySelector("#main .copyable-area > div:nth-child(2)") ||
                                    document.querySelector("#main .copyable-area > div") ||
                                    document.querySelector("#main [role='application']");
                    if (container) {
                        container.scrollTop = 0;
                    }
                """)
                try:
                    pagina.mouse.wheel(0, -3000)
                except Exception:
                    pass
                pagina.keyboard.press("PageUp")
                time.sleep(1.8)  # Tempo para renderização e requisição de histórico

            # Ordenação cronológica das mensagens extraídas
            lista_final = sorted(mensagens_coletadas.values(), key=lambda x: x["data_hora"])

            # Detecta o JID do grupo a partir do WhatsApp Web se presente
            grupo_jid_identificado = None
            for msg in lista_final:
                mid = msg.get("id") or ""
                match_jid = re.search(r"(\d+@g\.us)", mid)
                if match_jid:
                    grupo_jid_identificado = match_jid.group(1)
                    break

            grupo_id = gerar_grupo_id(nome_grupo, jid=grupo_jid_identificado)
            coleta_id = str(uuid.uuid4())
            coletado_em = datetime.utcnow().isoformat()

            for msg in lista_final:
                msg["coleta_id"] = coleta_id
                msg["grupo_id"] = grupo_id
                msg["grupo_nome"] = nome_grupo
                msg["comunidade_nome"] = nome_comunidade
                msg["coletado_em"] = coletado_em
                if unidade_tempo == "meses" or tipo_filtro == "mes":
                    msg["meses_back"] = int(valor)
                    msg["semanas_back"] = int(valor) * 4
                else:
                    msg["meses_back"] = 0
                    msg["semanas_back"] = int(valor)

            print(f"\nTotal de mensagens extraídas com sucesso: {len(lista_final)}")
            print("=" * 60)

            for i, m in enumerate(lista_final, 1):
                status_reply = "[REPLY]" if m.get("is_reply") else "[MENSAGEM]"
                attachment_flag = " [ANEXO]" if m.get("has_attachments") else ""
                print(f"[{i}] {status_reply}{attachment_flag} [{m['data_hora_str']}] {m['remetente']}: {m['texto']}")
                if m.get("is_reply") and m.get("reply_data"):
                    print(f"   └──> Em resposta a {m['reply_data']['autor_citado']}: \"{m['reply_data']['texto_citado']}\"")

                if m.get("has_attachments"):
                    anexos = m.get("attachments", [])
                    tipos = sorted({a.get("type") for a in anexos if a.get("type")})
                    label_map = {
                        "image": "imagem",
                        "video": "vídeo",
                        "audio": "áudio",
                        "document": "documento",
                        "sticker": "figurinha",
                        "poll": "enquete",
                    }
                    tipos_label = [label_map.get(t, t) for t in tipos]
                    if tipos_label:
                        print(f"   └──> Contém: {', '.join(tipos_label)}")

                print("-" * 50)

            # Persistir mensagens no banco local (SQLite) incrementalmente para o grupo ativo
            try:
                init_db()
                novas_inseridas = save_messages(lista_final)
                total_no_banco = count_messages()
                set_active_group(group_id=grupo_id, group_name=nome_grupo, total_messages=total_no_banco)
                print(f"[INFO] {novas_inseridas} mensagens processadas ({len(lista_final)} coletadas nesta rodada, {total_no_banco} mensagens totais no banco). Grupo ativo: '{nome_grupo}'")
            except Exception as e:
                print(f"[ERRO] Falha ao salvar mensagens ou estado: {e}")

            time.sleep(3)

        except Exception as e:
            msg_erro = str(e).lower()
            if "target page, context or browser has been closed" in msg_erro or "closed" in msg_erro:
                print("\n[INFO] O navegador foi fechado pelo usuário.")
            else:
                raise e
        finally:
            try:
                contexto.close()
            except Exception:
                pass


if __name__ == "__main__":
    extrair_dados_comunidade()