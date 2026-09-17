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
    create_backup,
    create_new_consulta_db,
    gerar_grupo_id,
    init_db,
    record_coleta_historico,
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
    Prioriza sempre correspondência exata de título antes de buscas parciais.
    """
    candidatos_brutos = []
    
    # Prioridade 1 é SEMPRE o nome completo original fornecido
    for valor in [nome, nome_alternativo]:
        if not valor or not valor.strip():
            continue
        v = valor.strip()
        candidatos_brutos.append(v)
        
        # Se contiver delimitadores (pipe ou hífen), adiciona também partes secundárias
        if "|" in v:
            partes = [p.strip() for p in v.split("|") if p.strip()]
            if partes:
                candidatos_brutos.append(partes[0])
                candidatos_brutos.append(" ".join(partes))
        elif "-" in v:
            partes = [p.strip() for p in v.split("-") if p.strip()]
            if partes:
                candidatos_brutos.append(partes[0])
                candidatos_brutos.append(" ".join(partes))

    # Remove duplicatas preservando a ordem de prioridade
    candidatos = list(dict.fromkeys(candidatos_brutos))
    print(f"[Busca] Candidatos ordenados para busca do grupo: {candidatos}")

    # 1. Verifica se o chat desejado já está aberto no painel principal (#main) com match exato
    try:
        header_title = pagina.locator("#main header span[title], #main header div[title], #main header h2").first
        if header_title.count() > 0:
            current_chat = header_title.get_attribute("title") or header_title.inner_text() or ""
            current_norm = normalizar_texto_busca(current_chat)
            for c in candidatos:
                c_norm = normalizar_texto_busca(c)
                if c_norm and c_norm == current_norm:
                    print(f"[Busca] Chat com correspondência exata já está aberto no painel: '{current_chat}'")
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

        print(f"[Busca] Pesquisando grupo no WhatsApp Web: '{termo}'...")

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
                time.sleep(0.3)
                # Limpa qualquer busca anterior
                pagina.keyboard.press("Control+A")
                pagina.keyboard.press("Backspace")
                time.sleep(0.2)
                # Digita termo na caixa de busca
                pagina.keyboard.type(termo, delay=35)
                time.sleep(2.0)  # Aguarda o WhatsApp filtrar e renderizar os resultados
            except Exception as e:
                print(f"[Busca - Aviso] Falha ao interagir com campo de busca: {e}")
                try:
                    pagina.keyboard.type(termo, delay=35)
                    time.sleep(2.0)
                except Exception:
                    pass

        # 3. Coleta os itens de resultado de forma estruturada (extraindo estritamente o título)
        # Executa no navegador para extrair título real e índice de cada linha sem ler mensagens de prévia
        itens_encontrados = pagina.evaluate("""
            () => {
                const results = [];
                const rows = document.querySelectorAll("#pane-side div[role='listitem'], #pane-side div[role='gridcell'], #pane-side div[data-testid='cell-frame-container']");
                
                rows.forEach((row, index) => {
                    let titleEl = row.querySelector("div[data-testid='cell-frame-title'] span[title], span[data-testid='chat-title'], div._ak8q span[title]");
                    if (!titleEl) {
                        const allSpans = row.querySelectorAll("span[title]");
                        if (allSpans && allSpans.length > 0) {
                            titleEl = allSpans[0];
                        }
                    }
                    if (titleEl) {
                        const rawTitle = (titleEl.getAttribute("title") || titleEl.innerText || "").trim();
                        const cleanTitle = rawTitle.replace(/[\\u200E\\u200F\\u202A-\\u202E]/g, "").trim();
                        if (cleanTitle) {
                            results.push({
                                index: index,
                                title: cleanTitle
                            });
                        }
                    }
                });
                return results;
            }
        """)

        # Estratégia em 2 etapas:
        # Etapa 1: Busca correspondência EXATA de título com qualquer candidato
        target_index = None
        target_title = None

        for item in itens_encontrados:
            t_norm = normalizar_texto_busca(item.get("title", ""))
            for c in candidatos:
                if t_norm == normalizar_texto_busca(c):
                    target_index = item["index"]
                    target_title = item["title"]
                    print(f"[Busca] Correspondência EXATA encontrada no resultado #{target_index}: '{target_title}'")
                    break
            if target_index is not None:
                break

        # Etapa 2: Se não houver correspondência exata, aceita correspondência onde o título contém o termo
        if target_index is None:
            for item in itens_encontrados:
                t_norm = normalizar_texto_busca(item.get("title", ""))
                if termo_norm in t_norm:
                    target_index = item["index"]
                    target_title = item["title"]
                    print(f"[Busca] Correspondência por substring encontrada no resultado #{target_index}: '{target_title}'")
                    break

        # 4. Clica no item identificado
        if target_index is not None:
            try:
                rows_locator = pagina.locator("#pane-side div[role='listitem'], #pane-side div[role='gridcell'], #pane-side div[data-testid='cell-frame-container']")
                alvo = rows_locator.nth(target_index)
                if alvo.is_visible():
                    alvo.click(timeout=6000, force=True)
                else:
                    pagina.keyboard.press("Enter")
            except Exception as e:
                print(f"[Busca - Aviso] Falha ao clicar no elemento do card: {e}")
                try:
                    pagina.keyboard.press("Enter")
                except Exception:
                    pass

            time.sleep(2.0)

            # Verifica se o painel #main carregou
            try:
                pagina.wait_for_selector("#main", timeout=12000)
                print(f"[Busca] Sucesso: conversa '{target_title}' aberta no painel principal.")
                
                # Foca no contêiner de mensagens com segurança
                try:
                    pagina.evaluate("""
                        () => {
                            const panel = document.querySelector("#main div[data-testid='conversation-panel-messages']") ||
                                          document.querySelector("#main div[tabindex='-1']") ||
                                          document.querySelector("#main header");
                            if (panel) panel.focus();
                        }
                    """)
                except Exception:
                    pass
                return True
            except Exception:
                pass

        # Se não encontrou neste candidato, limpa a caixa de pesquisa antes do próximo
        try:
            pagina.keyboard.press("Escape")
            time.sleep(0.4)
        except Exception:
            pass

    # 5. Fallback: Varredura por rolagem direta na lista de conversas
    print("[Busca] Tentando fallback por rolagem direta na lista de conversas...")
    try:
        for _ in range(4):
            cards = pagina.query_selector_all("#pane-side div[role='listitem'], #pane-side div[role='gridcell']")
            for card in cards:
                title_el = card.query_selector("div[data-testid='cell-frame-title'] span[title], span[title]")
                if not title_el:
                    continue
                title = (title_el.get_attribute("title") or title_el.inner_text() or "").strip()
                title_norm = normalizar_texto_busca(title)
                for c in candidatos:
                    c_norm = normalizar_texto_busca(c)
                    if c_norm and (c_norm == title_norm or c_norm in title_norm):
                        print(f"[Busca] Chat localizado no fallback: '{title}'. Clicando...")
                        card.click()
                        time.sleep(2.0)
                        pagina.wait_for_selector("#main", timeout=10000)
                        return True
            # Rola a lista lateral para carregar mais conversas
            pagina.evaluate("""
                let pane = document.querySelector("#pane-side");
                if (pane) pane.scrollTop += 500;
            """)
            time.sleep(0.8)
    except Exception as e:
        print(f"[Busca] Erro durante o fallback de rolagem: {e}")

    print("[Busca] Nenhum chat localizado para os candidatos fornecidos.")
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

        # Impede abertura acidental de novas abas/janelas provenientes de cliques em links de mensagens
        def _fechar_aba_acidental(nova_aba):
            try:
                nova_aba.close()
            except Exception:
                pass

        contexto.on("page", _fechar_aba_acidental)

        try:
            pagina = contexto.pages[0] if contexto.pages else contexto.new_page()

            # Bloqueio rigoroso de navegação externa e cliques em links no WhatsApp Web
            pagina.add_init_script("""
                // 1. Sobrescreve window.open para neutralizar popups
                window.open = function() { return null; };

                // 2. Intercepta todos os eventos de clique em links e previews na fase de captura
                ['click', 'auxclick', 'mousedown', 'mouseup', 'dblclick'].forEach(evtName => {
                    document.addEventListener(evtName, function(e) {
                        const target = e.target;
                        if (!target) return;
                        const isLink = target.closest('a, [role="link"], [data-testid*="link"], [data-testid*="preview"], [data-js-link="true"]');
                        if (isLink) {
                            e.preventDefault();
                            e.stopPropagation();
                            e.stopImmediatePropagation();
                            return false;
                        }
                    }, true);
                });

                // 3. Injeta regra CSS global para desativar interações de ponteiro em links e cards
                const injectAntiLinkCSS = () => {
                    if (document.getElementById('whatsapp-anti-link-style')) return;
                    const style = document.createElement('style');
                    style.id = 'whatsapp-anti-link-style';
                    style.innerHTML = `
                        a, 
                        [role="link"], 
                        [data-testid*="link"], 
                        [data-testid*="preview"],
                        div[data-testid="link-preview"],
                        div[data-testid="media-url-preview"],
                        a *, 
                        [role="link"] *, 
                        [data-testid*="link"] *, 
                        [data-testid*="preview"] * {
                            pointer-events: none !important;
                            cursor: default !important;
                            user-select: text !important;
                        }
                    `;
                    (document.head || document.documentElement).appendChild(style);
                };

                // 4. MutationObserver contínuo para desarmar hrefs e targets de todos os links dinâmicos
                const observer = new MutationObserver(() => {
                    document.querySelectorAll('a[href], [data-testid*="preview"]').forEach(el => {
                        if (el.tagName === 'A') {
                            el.removeAttribute('href');
                            el.removeAttribute('target');
                        }
                        el.style.pointerEvents = 'none';
                    });
                });

                const startArmor = () => {
                    injectAntiLinkCSS();
                    observer.observe(document.documentElement, { childList: true, subtree: true });
                };

                if (document.readyState === 'loading') {
                    document.addEventListener('DOMContentLoaded', startArmor);
                } else {
                    startArmor();
                }
            """)

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

            print("\n--- Iniciando rolagem incremental e raspagem contínua ---")

            # Estrutura de armazenamento com deduplicação nativa por ID
            mensagens_coletadas = {}

            atingiu_limite = False
            tentativas_sem_novos_dados = 0
            max_tentativas = 35
            seletor_baloes = "#main div[data-id], #main div.message-in, #main div.message-out, #main div[data-pre-plain-text]"

            while not atingiu_limite and tentativas_sem_novos_dados < max_tentativas:
                baloes_visiveis = pagina.query_selector_all(seletor_baloes)
                total_antes = len(mensagens_coletadas)
                data_mais_antiga_rodada = None

                # Raspagem imediata dos balões presentes no DOM atual
                for balao in baloes_visiveis:
                    dados = extrair_dados_balao(balao)
                    if not dados:
                        continue

                    # Rastreia a data da mensagem mais antiga nesta rodada
                    if data_mais_antiga_rodada is None or dados["data_hora"] < data_mais_antiga_rodada:
                        data_mais_antiga_rodada = dados["data_hora"]

                    # Interrupção temporal: verifica se atingiu mensagens anteriores à janela
                    if dados["data_hora"] < data_limite:
                        print(f"\n[Alerta] Alcançou mensagem de {dados['data_hora_str']} (Anterior a {data_limite.strftime('%d/%m/%Y %H:%M')}). Encerrando scroll...")
                        atingiu_limite = True
                        break

                    # Adiciona ao dicionário (se já existir, atualiza sem duplicar)
                    mensagens_coletadas[dados["id"]] = dados

                if atingiu_limite:
                    break

                # Verifica progresso de novas mensagens coletadas
                novas_nesta_rodada = len(mensagens_coletadas) - total_antes
                if novas_nesta_rodada == 0:
                    tentativas_sem_novos_dados += 1
                    if tentativas_sem_novos_dados % 3 == 0:
                        print(f"[Aguardando histórico...] Tentativa {tentativas_sem_novos_dados}/{max_tentativas} aguardando carregamento do WhatsApp...")
                else:
                    tentativas_sem_novos_dados = 0
                    msg_antiga_txt = data_mais_antiga_rodada.strftime("%d/%m/%Y %H:%M") if data_mais_antiga_rodada else "-"
                    print(f">> Total coletado: {len(mensagens_coletadas)} msgs | Mais antiga lida: {msg_antiga_txt} | Meta: até {data_limite.strftime('%d/%m/%Y %H:%M')}")

                # Executa a rolagem para cima com detecção dinâmica do container ativo e disparo de eventos
                pagina.evaluate("""
                    (tentativa) => {
                        // 1. Desativa links para evitar navegações acidentais
                        document.querySelectorAll('#main a').forEach(a => {
                            a.removeAttribute('href');
                            a.removeAttribute('target');
                            a.style.pointerEvents = 'none';
                        });

                        // 2. Localiza dinamicamente o elemento com barra de rolagem vertical ativa
                        function getScrollElement() {
                            const candidates = Array.from(document.querySelectorAll('#main div, #main [role="application"], #main [data-testid="conversation-panel-messages"]'));
                            for (const el of candidates) {
                                const style = window.getComputedStyle(el);
                                if ((style.overflowY === 'auto' || style.overflowY === 'scroll') && el.scrollHeight > el.clientHeight) {
                                    return el;
                                }
                            }
                            return document.querySelector("#main div[data-testid='conversation-panel-messages']") ||
                                   document.querySelector("#main div[tabindex='-1']") || 
                                   document.querySelector("div[data-tab='8']") ||
                                   document.querySelector("#main .copyable-area > div:nth-child(2)") ||
                                   document.querySelector("#main [role='application']");
                        }

                        const container = getScrollElement();
                        if (container) {
                            // Se estiver estagnado há mais de 3 tentativas, faz um pequeno jitter (desce e sobe) para forçar o listener do React
                            if (tentativa > 2 && tentativa % 2 === 0) {
                                container.scrollTop = 120;
                            } else {
                                container.scrollTop = 0;
                            }
                            // Dispara evento nativo de scroll
                            container.dispatchEvent(new Event('scroll', { bubbles: true }));
                        }

                        // 3. Rola a primeira mensagem visível para a visualização
                        const firstMsg = document.querySelector("#main div[data-id], #main div.message-in, #main div.message-out");
                        if (firstMsg) {
                            try {
                                firstMsg.scrollIntoView({ block: 'start', behavior: 'instant' });
                            } catch (e) {}
                        }
                    }
                """, tentativas_sem_novos_dados)

                # Simula pressão suave de PageUp / ArrowUp para acordar o virtual scroll do WhatsApp
                try:
                    pagina.keyboard.press("PageUp")
                except Exception:
                    pass

                time.sleep(1.5)  # Intervalo ideal para renderização e requisição de lotes

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

            # Persistir mensagens em um banco de dados SQLite novo, independente e isolado para esta consulta
            try:
                # Cria novo banco isolado para esta consulta e define como ativo
                consulta_filename, consulta_path = create_new_consulta_db(group_name=nome_grupo)
                print(f"[INFO] Novo banco de dados independente criado para esta consulta: {consulta_filename}")

                novas_inseridas = save_messages(lista_final, db_path=consulta_path)
                total_no_banco = count_messages(db_path=consulta_path, grupo_id=grupo_id, grupo_nome=nome_grupo)
                set_active_group(group_id=grupo_id, group_name=nome_grupo, total_messages=total_no_banco, db_filename=consulta_filename)

                # Registra auditoria da coleta no histórico do banco independente
                record_coleta_historico({
                    "id": coleta_id,
                    "grupo_id": grupo_id,
                    "grupo_nome": nome_grupo,
                    "comunidade_nome": nome_comunidade,
                    "executado_em": datetime.now().isoformat(),
                    "unidade_tempo": unidade_tempo,
                    "valor": valor,
                    "tipo_filtro": tipo_filtro or "tempo",
                    "total_extraido": len(lista_final),
                    "total_acumulado": total_no_banco,
                    "status": "Concluído",
                    "detalhes": {
                        "novas_inseridas": novas_inseridas,
                        "coleta_id": coleta_id,
                        "banco_consulta": consulta_filename,
                    }
                }, db_path=consulta_path)

                # Cria snapshot de backup automático pós-extração
                try:
                    create_backup(
                        tag="auto_extracao",
                        description=f"Snapshot automático após coleta de '{nome_grupo}' ({len(lista_final)} msgs coletadas)",
                        db_path=consulta_path,
                    )
                except Exception as b_err:
                    print(f"[AVISO] Falha ao criar snapshot automático de backup: {b_err}")

                print(f"[INFO] {novas_inseridas} mensagens gravadas no banco independente '{consulta_filename}'. Consulta ativa: '{nome_grupo}'")
            except Exception as e:
                print(f"[ERRO] Falha ao salvar mensagens no banco da consulta: {e}")

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