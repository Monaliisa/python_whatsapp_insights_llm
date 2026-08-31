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
    """Retorna o caminho absoluto para o arquivo SQLite de mensagens."""
    return str(get_data_dir() / "messages.db")


def get_state_file_path() -> Path:
    """Retorna o caminho do arquivo JSON de estado/sessão da aplicação."""
    return get_data_dir() / "app_state.json"


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
