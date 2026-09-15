from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


from services.paths import get_session_dir


def get_session_path() -> str:
    return str(get_session_dir())


def verificar_status_sessao() -> bool:
    """
    Verifica de forma rápida e não-intrusiva se existe um perfil de sessão persistente
    com dados de armazenamento do WhatsApp Web.
    """
    caminho_sessao = get_session_path()
    default_dir = os.path.join(caminho_sessao, "Default")
    if not os.path.exists(default_dir):
        return False

    # Verifica se existem pastas típicas de armazenamento do Chromium (IndexedDB / Local Storage)
    indexed_db = os.path.join(default_dir, "IndexedDB")
    local_storage = os.path.join(default_dir, "Local Storage")
    
    if os.path.exists(indexed_db) and os.path.isdir(indexed_db) and os.listdir(indexed_db):
        return True
    if os.path.exists(local_storage) and os.path.isdir(local_storage) and os.listdir(local_storage):
        return True

    return False


def desconectar_sessao() -> tuple[bool, str]:
    """
    Remove os dados da pasta de sessão do WhatsApp para deslogar a conta local.
    """
    caminho_sessao = get_session_path()
    if not os.path.exists(caminho_sessao):
        return True, "Nenhuma sessão ativa encontrada."

    try:
        for item in os.listdir(caminho_sessao):
            item_path = os.path.join(caminho_sessao, item)
            try:
                if os.path.isdir(item_path):
                    shutil.rmtree(item_path, ignore_errors=True)
                else:
                    os.remove(item_path)
            except Exception:
                pass
        return True, "Sessão do WhatsApp encerrada e dados locais limpos com sucesso."
    except Exception as e:
        return False, f"Erro ao limpar dados de sessão: {e}"


def extrair_grupos_do_painel(pagina, max_scrolls: int = 6) -> list[str]:
    """
    Varre o painel lateral (#pane-side) do WhatsApp Web e extrai estritamente os nomes dos grupos,
    ignorando mensagens de prévia, números de telefone e chats privados.
    """
    import re
    grupos_encontrados = set()
    try:
        pagina.wait_for_selector("#pane-side", timeout=12000)
    except Exception:
        return []

    # 1. Tenta ativar o filtro nativo de 'Grupos' no topo da lista do WhatsApp Web
    filtro_ativado = False
    seletores_filtro = [
        "#side button:has-text('Grupos')",
        "#side button:has-text('Groups')",
        "#side div[role='button']:has-text('Grupos')",
        "#side div[role='button']:has-text('Groups')",
        "button[aria-label*='Grupos']",
        "button[aria-label*='Groups']",
        "button[title*='Grupos']",
        "button[title*='Groups']",
        "div[role='tab']:has-text('Grupos')",
    ]
    for sel in seletores_filtro:
        try:
            elem = pagina.locator(sel).first
            if elem.count() > 0 and elem.is_visible():
                elem.click()
                time.sleep(1.2)
                filtro_ativado = True
                print(">> [INFO] Filtro nativo 'Grupos' ativado no WhatsApp Web.")
                break
        except Exception:
            pass

    for _ in range(max_scrolls):
        # Executa extração segura no contexto do navegador
        # Pega ESTRITAMENTE o elemento de título do chat e ignora qualquer texto de mensagem/prévia
        chats_capturados = pagina.evaluate("""
            () => {
                const items = [];
                // Localiza todos os containers de conversas no painel lateral
                const rows = document.querySelectorAll("#pane-side div[role='listitem'], #pane-side div[role='gridcell'], #pane-side div[data-testid='cell-frame-container'], #pane-side div[tabindex='-1']");
                
                rows.forEach(row => {
                    // O título do chat fica no cabeçalho do card (cell-frame-title)
                    // NUNCA no cell-frame-secondary (que contém a última mensagem)
                    let titleEl = row.querySelector("div[data-testid='cell-frame-title'] span[title], span[data-testid='chat-title'], div._ak8q span[title]");
                    
                    if (!titleEl) {
                        const titleContainer = row.querySelector("div[data-testid='cell-frame-title'], div._ak8q");
                        if (titleContainer) {
                            titleEl = titleContainer.querySelector("span[title]");
                        }
                    }

                    if (!titleEl) {
                        // Pega apenas o primeiro span com title dentro do card (nome da conversa)
                        const allSpans = row.querySelectorAll("span[title]");
                        if (allSpans && allSpans.length > 0) {
                            titleEl = allSpans[0];
                        }
                    }

                    if (titleEl) {
                        const rawTitle = (titleEl.getAttribute("title") || titleEl.innerText || "").trim();
                        // Remove caracteres invisíveis e bidi unicode (ex: \\u202a)
                        const cleanTitle = rawTitle.replace(/[\\u200E\\u200F\\u202A-\\u202E]/g, "").trim();
                        
                        // Verifica se tem ícone indicativo de grupo/comunidade
                        const hasGroupIcon = !!row.querySelector("span[data-icon*='group'], span[data-icon*='community'], span[data-icon*='avatar-group']");
                        
                        if (cleanTitle) {
                            items.push({
                                title: cleanTitle,
                                isGroup: hasGroupIcon
                            });
                        }
                    }
                });
                return items;
            }
        """)

        # Processa e valida os títulos capturados
        ignorar = {
            "você", "you", "meta ai", "whatsapp", "mensagens favoritas",
            "starred messages", "avisos", "status", "rascunhos", "drafts", "nome desconhecido"
        }
        for item in chats_capturados:
            titulo = item.get("title", "").strip()
            is_group_icon = item.get("isGroup", False)
            
            if not titulo or len(titulo) < 2 or len(titulo) > 100:
                continue

            # Remove espaços e símbolos para testar se é número de telefone puro
            digitos_puros = re.sub(r"[\s\-\(\)\+\.]", "", titulo)
            if digitos_puros.isdigit() or re.match(r"^\+?[0-9\s\-\(\)\.]{7,}$", titulo):
                continue

            # Ignora timestamps (ex: "15:30", "12/04/2026")
            if re.match(r"^(\d{1,2}:\d{2}|\d{1,2}\/\d{1,2}(\/\d{2,4})?)$", titulo):
                continue

            if titulo.lower() in ignorar:
                continue

            # Se o filtro de Grupos do WhatsApp Web foi ativado, todos os chats exibidos são grupos
            # Se o filtro não foi ativado, exige ícone de grupo ou delimitador característico
            if filtro_ativado or is_group_icon or "|" in titulo or "-" in titulo:
                grupos_encontrados.add(titulo)

        # Rola o painel lateral para carregar mais conversas
        try:
            pagina.evaluate("""
                let pane = document.querySelector("#pane-side");
                if (pane) pane.scrollTop += 600;
            """)
            time.sleep(0.8)
        except Exception:
            break

    # Se ativou o filtro, retorna para a aba 'Tudo' para manter a interface natural
    if filtro_ativado:
        for sel_tudo in ["#side button:has-text('Tudo')", "#side button:has-text('All')", "button[title*='Tudo']"]:
            try:
                el_tudo = pagina.locator(sel_tudo).first
                if el_tudo.count() > 0 and el_tudo.is_visible():
                    el_tudo.click()
                    break
            except Exception:
                pass

    return sorted(list(grupos_encontrados), key=lambda s: s.lower())


def sincronizar_grupos_whatsapp(headless: bool = False, timeout_segundos: int = 60) -> list[str]:
    """
    Conecta temporariamente ao WhatsApp Web usando a sessão salva, raspa a lista de grupos
    e salva o resultado no catálogo data/groups_catalog.json.
    """
    from services.storage import save_catalog_groups

    caminho_sessao = get_session_path()
    if not verificar_status_sessao():
        print("[AVISO] Nenhuma sessão ativa encontrada para sincronizar grupos.")
        return []

    with sync_playwright() as p:
        contexto = p.chromium.launch_persistent_context(
            user_data_dir=caminho_sessao,
            headless=headless,
            args=["--start-maximized"],
            no_viewport=True,
        )
        contexto.on("page", lambda p: p.close())
        try:
            pagina = contexto.pages[0] if contexto.pages else contexto.new_page()
            pagina.add_init_script("window.open = function() { return null; };")
            pagina.goto("https://web.whatsapp.com")

            seletores_painel = "#pane-side, header[data-testid='chatlist-header']"
            pagina.wait_for_selector(seletores_painel, timeout=timeout_segundos * 1000)

            print("Extraindo lista de grupos do WhatsApp Web...")
            grupos = extrair_grupos_do_painel(pagina, max_scrolls=6)
            if grupos:
                save_catalog_groups(grupos)
                print(f"[SUCESSO] {len(grupos)} grupos sincronizados no catálogo local!")
            return grupos
        except Exception as e:
            print(f"[ERRO] Falha ao sincronizar grupos do WhatsApp: {e}")
            return []
        finally:
            try:
                contexto.close()
            except Exception:
                pass


def iniciar_coletor(timeout_segundos: int = 300) -> bool:
    """
    Abre o navegador e gerencia o ciclo de autenticação no WhatsApp Web,
    aguardando o usuário escanear o QR Code sem fechar prematuramente.
    """
    caminho_sessao = get_session_path()
    
    with sync_playwright() as p:
        print("\n" + "=" * 55)
        print("          CONECTOR DE SESSÃO WHATSAPP WEB")
        print("=" * 55)
        print("Iniciando o navegador Chromium...")
        
        contexto = p.chromium.launch_persistent_context(
            user_data_dir=caminho_sessao,
            headless=False,
            args=["--start-maximized"],
            no_viewport=True
        )
        
        contexto.on("page", lambda p: p.close())
        try:
            pagina = contexto.pages[0] if contexto.pages else contexto.new_page()
            pagina.add_init_script("window.open = function() { return null; };")
            
            print("Acessando https://web.whatsapp.com ...")
            pagina.goto("https://web.whatsapp.com")
            
            seletores_painel_conectado = "#pane-side, div[contenteditable='true'][data-tab='3'], header[data-testid='chatlist-header']"

            print("Aguardando carregamento da interface...")

            conectado = False
            # Verifica se já está conectado de imediato
            try:
                pagina.wait_for_selector(seletores_painel_conectado, timeout=12000)
                print(">> [STATUS] Sessão já conectada e ativa no WhatsApp Web!")
                conectado = True
            except PlaywrightTimeoutError:
                pass

            if not conectado:
                # Se não estiver conectado de imediato, orienta o usuário a escanear o QR Code
                print("\n" + "-" * 55)
                print("[ATENÇÃO] Por favor, aponte a câmera do seu celular e")
                print("escanie o QR Code na tela para autenticar.")
                print(f"O navegador permanecerá aberto aguardando a sincronização (até {timeout_segundos}s)...")
                print("-" * 55 + "\n")

                # Aguarda a conclusão da autenticação até o timeout configurado
                pagina.wait_for_selector(seletores_painel_conectado, timeout=timeout_segundos * 1000)
                print("\n>> [SUCESSO] WhatsApp Web autenticado e conectado com sucesso!")
                print("Sessão salva localmente para próximas coletas.")
                conectado = True

            if conectado:
                try:
                    print("Sincronizando lista de grupos conhecidos...")
                    grupos = extrair_grupos_do_painel(pagina, max_scrolls=4)
                    if grupos:
                        from services.storage import save_catalog_groups
                        save_catalog_groups(grupos)
                        print(f">> [INFO] {len(grupos)} grupos catalogados no arquivo data/groups_catalog.json.")
                except Exception as err:
                    print(f"[AVISO] Não foi possível catalogar os grupos automaticamente: {err}")

                time.sleep(3)
                return True

            return False

        except PlaywrightTimeoutError:
            print(f"\n[AVISO] Tempo limite de {timeout_segundos}s atingido sem leitura do QR Code.")
            return False
        except Exception as e:
            msg_erro = str(e).lower()
            if "target page, context or browser has been closed" in msg_erro or "closed" in msg_erro:
                print("\n[INFO] Navegador fechado pelo usuário.")
            else:
                print(f"\n[ERRO] Ocorreu um problema durante a conexão: {e}")
            return False
        finally:
            try:
                contexto.close()
            except Exception:
                pass


if __name__ == "__main__":
    iniciar_coletor()


