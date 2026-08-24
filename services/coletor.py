from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


def get_session_path() -> str:
    projeto = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    caminho = os.path.join(projeto, "sessao_whatsapp")
    os.makedirs(caminho, exist_ok=True)
    return caminho


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
        
        try:
            pagina = contexto.pages[0] if contexto.pages else contexto.new_page()
            
            print("Acessando https://web.whatsapp.com ...")
            pagina.goto("https://web.whatsapp.com")
            
            seletores_painel_conectado = "#pane-side, div[contenteditable='true'][data-tab='3'], header[data-testid='chatlist-header']"

            print("Aguardando carregamento da interface...")

            # Verifica se já está conectado de imediato
            try:
                pagina.wait_for_selector(seletores_painel_conectado, timeout=12000)
                print(">> [STATUS] Sessão já conectada e ativa no WhatsApp Web!")
                time.sleep(3)
                return True
            except PlaywrightTimeoutError:
                pass

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
            time.sleep(4)
            return True

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

