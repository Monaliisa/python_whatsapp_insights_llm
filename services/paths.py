from __future__ import annotations

import os
import sys
from pathlib import Path


def get_base_dir() -> Path:
    """
    Retorna o diretório base gravável onde a aplicação está sendo executada.
    - Se empacotado pelo PyInstaller (.exe), retorna a pasta onde o executável reside.
    - Se em desenvolvimento (Python normal), retorna a raiz do repositório.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def get_bundle_dir() -> Path:
    """
    Retorna o diretório somente-leitura onde estão os recursos embutidos (templates, assets).
    - Se empacotado pelo PyInstaller (.exe), aponta para a pasta temporária sys._MEIPASS.
    - Se em desenvolvimento, aponta para a raiz do repositório.
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS).resolve()
    return Path(__file__).resolve().parent.parent


def get_data_dir() -> Path:
    """Retorna o diretório para armazenamento persistente de dados (banco SQLite, CSVs)."""
    data_dir = get_base_dir() / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_session_dir() -> Path:
    """Retorna o diretório para persistência do perfil de sessão do WhatsApp Web."""
    session_dir = get_base_dir() / "sessao_whatsapp"
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def get_db_path() -> str:
    """
    Retorna o caminho absoluto para o arquivo SQLite de mensagens da consulta ativa.
    Se houver uma consulta selecionada no app_state.json, usa ela.
    Caso contrário, seleciona a consulta mais recente ou o banco padrão.
    """
    state_file = get_state_file_path()
    consultas_dir = get_consultas_dir()

    if state_file.exists():
        try:
            import json
            with open(state_file, "r", encoding="utf-8") as f:
                state = json.load(f)
                active_db = state.get("active_db_filename")
                if active_db:
                    target = consultas_dir / active_db
                    if target.exists():
                        return str(target)
                    legacy = get_data_dir() / active_db
                    if legacy.exists():
                        return str(legacy)
        except Exception:
            pass

    # Se existirem consultas salvas em data/consultas/, usa a mais recente
    consultas = sorted(consultas_dir.glob("*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    if consultas:
        return str(consultas[0])

    return str(get_data_dir() / "messages.db")


def get_state_file_path() -> Path:
    """Retorna o caminho do arquivo JSON de estado/sessão da aplicação."""
    return get_data_dir() / "app_state.json"


def get_groups_catalog_path() -> Path:
    """Retorna o caminho do arquivo JSON de catálogo de grupos catalogados do WhatsApp."""
    return get_data_dir() / "groups_catalog.json"


def get_backups_dir() -> Path:
    """Retorna o diretório para armazenamento persistente de snapshots e cópias de backup (.db, .json)."""
    backups_dir = get_data_dir() / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)
    return backups_dir


def get_consultas_dir() -> Path:
    """Retorna o diretório para armazenamento de bancos de dados (.db) de consultas independentes."""
    consultas_dir = get_data_dir() / "consultas"
    consultas_dir.mkdir(parents=True, exist_ok=True)
    return consultas_dir


def get_templates_dir() -> Path:
    """Retorna o diretório dos templates HTML da interface web."""
    return get_bundle_dir() / "ui" / "templates"


def get_views_dir() -> Path:
    """Retorna o diretório de views da interface web."""
    return get_bundle_dir() / "ui" / "views"


def setup_environment() -> None:
    """
    Configura variáveis de ambiente necessárias para a execução consistente
    tanto em ambiente de desenvolvimento quanto em produção (.exe).
    """
    # Garante que diretórios essenciais existam
    get_data_dir()
    get_session_dir()
    get_backups_dir()
    get_consultas_dir()

    # Configura o caminho dos navegadores do Playwright para executável (.exe / PyInstaller)
    if "PLAYWRIGHT_BROWSERS_PATH" not in os.environ:
        local_appdata = os.environ.get("LOCALAPPDATA")
        if local_appdata:
            ms_path = os.path.join(local_appdata, "ms-playwright")
            if os.path.exists(ms_path):
                os.environ["PLAYWRIGHT_BROWSERS_PATH"] = ms_path
            else:
                os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "0"
        else:
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "0"


# Executa setup básico ao carregar o módulo
setup_environment()

