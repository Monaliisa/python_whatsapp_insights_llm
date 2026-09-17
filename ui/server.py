from __future__ import annotations

import asyncio
import io
import json
import os
import queue
import re
import subprocess
import sys
import threading
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator

import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel

from services.coletor import (
    desconectar_sessao,
    iniciar_coletor,
    sincronizar_grupos_whatsapp,
    verificar_status_sessao,
)
from services.extrator import NOME_DO_GRUPO, extrair_dados_comunidade
from services.storage import (
    clear_active_group,
    count_messages,
    create_backup,
    create_new_consulta_db,
    delete_backup,
    delete_catalog_group,
    delete_catalog_groups,
    delete_consulta,
    delete_group_messages,
    detect_active_group_from_db,
    export_to_csv,
    export_to_json,
    fetch_message_by_id,
    fetch_messages_for_llm_range,
    fetch_recent,
    get_active_consulta_info,
    get_app_state,
    get_catalog_group_names,
    get_message_date_bounds,
    get_persistence_stats,
    import_from_csv_data,
    import_from_json_data,
    import_from_txt_data,
    init_db,
    list_backups,
    list_coletas_historico,
    list_consultas,
    list_grupos_historico,
    load_catalog_groups,
    record_coleta_historico,
    reset_messages_db,
    restore_backup,
    save_catalog_groups,
    set_active_consulta,
    set_active_group,
    wipe_all_data,
)
from services.llm import AnthropicService, GeminiService, MODELOS_DISPONIVEIS, ANALISES_PRE_PROGRAMADAS
from services.reports import ReportService
from services.paths import (
    get_backups_dir,
    get_base_dir,
    get_consultas_dir,
    get_data_dir,
    get_db_path,
    get_templates_dir,
    setup_environment,
)

BASE_DIR = get_base_dir()
TEMPLATES_DIR = get_templates_dir()
DATA_DIR = get_data_dir()
BACKUPS_DIR = get_backups_dir()
CONSULTAS_DIR = get_consultas_dir()
DEFAULT_EXPORT_PATH = DATA_DIR / "export_messages.csv"

app = FastAPI(title="WhatsApp Insights Web", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ExecutionState:
    def __init__(self):
        self.is_busy = False
        self.log_history: list[str] = []
        self.subscribers: list[queue.Queue] = []
        self.lock = threading.Lock()

    def add_log(self, message: str):
        timestamp = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        formatted = f"[{timestamp}] {message}"
        with self.lock:
            self.log_history.append(formatted)
            if len(self.log_history) > 500:
                self.log_history = self.log_history[-500:]
            for sub in list(self.subscribers):
                try:
                    sub.put_nowait(formatted)
                except Exception:
                    pass

    def subscribe(self) -> queue.Queue:
        q = queue.Queue()
        with self.lock:
            self.subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue):
        with self.lock:
            if q in self.subscribers:
                self.subscribers.remove(q)


state = ExecutionState()


class StdoutRedirector(io.TextIOBase):
    """Redireciona prints do stdout para os logs da UI e para o console original."""

    def __init__(self, original_stdout):
        super().__init__()
        self.original_stdout = original_stdout

    def write(self, s: str):
        if self.original_stdout:
            self.original_stdout.write(s)
            self.original_stdout.flush()
        text = s.strip()
        if text:
            state.add_log(text)
        return len(s)

    def flush(self):
        if self.original_stdout:
            self.original_stdout.flush()


class ColetaRequest(BaseModel):
    grupo: str = NOME_DO_GRUPO
    unidade_tempo: str = "dias"  # 'horas', 'dias', 'semanas', 'meses'
    valor: int = 7
    tipo_filtro: str | None = None


class SelectGroupRequest(BaseModel):
    grupo_id: str | None = None
    grupo_nome: str | None = None


class DeleteCatalogGroupsBatchRequest(BaseModel):
    group_ids: list[str]


class ExportRequest(BaseModel):
    destino: str = str(DEFAULT_EXPORT_PATH)
    grupo_id: str | None = None
    grupo_nome: str | None = None


class ImportRequest(BaseModel):
    content: str
    format: str = "csv"  # 'csv' ou 'json'
    filename: str | None = None


class ValidateLLMKeyRequest(BaseModel):
    api_key: str
    model: str = "gemini-2.5-flash"


class ChatLLMRequest(BaseModel):
    api_key: str
    model: str = "gemini-2.5-flash"
    prompt: str = ""
    start_ts: float | None = None
    end_ts: float | None = None
    tipo_analise: str | None = None
    historico: list[dict] | None = None


class CreateBackupRequest(BaseModel):
    tag: str = "manual"
    description: str = ""


class RestoreBackupRequest(BaseModel):
    filename: str


class SetActiveConsultaRequest(BaseModel):
    filename: str


class ReportSummaryRequest(BaseModel):
    api_key: str
    model: str = "gemini-2.5-flash"
    mes: str


@app.on_event("startup")
def startup_event():
    setup_environment()
    init_db(get_db_path())
    detected = detect_active_group_from_db(get_db_path())
    if detected:
        state.add_log(f"Interface inicializada. Grupo ativo carregado do banco local: '{detected['nome']}' ({detected['total_messages']} mensagens).")
    else:
        state.add_log("Interface Web inicializada com sucesso. Banco de mensagens pronto.")


@app.get("/", response_class=HTMLResponse)
async def serve_index():
    index_path = TEMPLATES_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="Template index.html não encontrado.")
    return HTMLResponse(content=index_path.read_text(encoding="utf-8"))


@app.get("/api/status")
async def get_status():
    db_path = get_db_path()
    has_session = verificar_status_sessao()
    total_messages = count_messages(db_path)
    active_group = detect_active_group_from_db(db_path) if total_messages > 0 else None
    app_state = get_app_state()

    return {
        "status": "online",
        "is_busy": state.is_busy,
        "has_session": has_session,
        "session_status": "connected" if has_session else "disconnected",
        "db_path": db_path,
        "db_exists": os.path.exists(db_path),
        "message_count": total_messages,
        "has_data": total_messages > 0,
        "default_grupo": NOME_DO_GRUPO,
        "default_export_path": str(DEFAULT_EXPORT_PATH),
        "app_state": app_state,
        "active_group": active_group,
    }


@app.get("/api/state")
async def get_state_endpoint():
    """Retorna o estado persistido do grupo ativo."""
    return {"success": True, "data": get_app_state()}


@app.post("/api/state/select-group")
async def select_group_endpoint(req: SelectGroupRequest):
    """Define o grupo ativo na memória e persiste no app_state.json."""
    if not req.grupo_nome and not req.grupo_id:
        return {"success": False, "message": "Nome ou ID do grupo não informado."}

    total = count_messages(db_path=get_db_path(), grupo_id=req.grupo_id, grupo_nome=req.grupo_nome)
    new_state = set_active_group(group_id=req.grupo_id, group_name=req.grupo_nome, total_messages=total)
    state.add_log(f"[Grupo] Grupo ativo definido: '{req.grupo_nome or req.grupo_id}' ({total} mensagens registradas).")
    return {"success": True, "state": new_state}


@app.post("/api/state/clear-group")
async def clear_group_endpoint():
    """Cria backup preventivo e reseta as mensagens locais da sessão para permitir trocar de grupo."""
    db_path = get_db_path()
    total_antes = count_messages(db_path)

    # 1. Cria backup de segurança automático caso haja mensagens
    if total_antes > 0:
        try:
            create_backup(
                tag="auto_troca_grupo",
                description="Backup preventivo gerado antes da troca de grupo ativo",
                db_path=db_path,
            )
        except Exception as e:
            print(f"[AVISO] Falha ao gerar backup pré-troca de grupo: {e}")

    # 2. Reseta o banco de dados messages.db e limpa o grupo ativo do app_state.json
    reset_messages_db(db_path)
    new_state = get_app_state()

    state.add_log(f"[Grupo] Base da sessão limpa ({total_antes} mensagens arquivadas em backup). Pronto para novo grupo.")
    return {
        "success": True,
        "state": new_state,
        "message": f"Base de mensagens resetada com sucesso ({total_antes} mensagens salvas em backup preventivo).",
    }


@app.post("/api/importar")
async def import_data(req: ImportRequest):
    if state.is_busy:
        return {"success": False, "message": "Existe outra tarefa em andamento. Aguarde."}

    content = req.content.strip()
    if not content:
        return {"success": False, "message": "Nenhum conteúdo fornecido para importação."}

    fmt = req.format.lower().strip()
    if req.filename:
        if req.filename.lower().endswith(".json"):
            fmt = "json"
        elif req.filename.lower().endswith(".csv"):
            fmt = "csv"
        elif req.filename.lower().endswith(".txt"):
            fmt = "txt"

    try:
        if fmt == "json":
            count, grupo_nome = import_from_json_data(content, db_path=get_db_path())
        elif fmt == "txt":
            # Tenta inferir o nome do grupo a partir do nome do arquivo (ex: "Conversa do WhatsApp com NomeDoGrupo.txt")
            grupo_nome_sugerido = "Conversa WhatsApp Importada"
            if req.filename:
                fn_clean = re.sub(r"\.txt$", "", req.filename, flags=re.IGNORECASE)
                fn_clean = re.sub(r"^Conversa do WhatsApp com\s*", "", fn_clean, flags=re.IGNORECASE)
                fn_clean = re.sub(r"^WhatsApp Chat with\s*", "", fn_clean, flags=re.IGNORECASE)
                if fn_clean.strip():
                    grupo_nome_sugerido = fn_clean.strip()
            count, grupo_nome = import_from_txt_data(content, nome_grupo=grupo_nome_sugerido, db_path=get_db_path())
        else:
            count, grupo_nome = import_from_csv_data(content, db_path=get_db_path())

        nome_arq = f" '{req.filename}'" if req.filename else ""
        msg = f"Importação concluída com sucesso! {count} mensagens processadas e preservadas a partir de{nome_arq}."
        if grupo_nome:
            msg += f" Grupo ativo fixado: '{grupo_nome}'."
        state.add_log(f"[Importação] {msg}")
        return {
            "success": True,
            "count": count,
            "grupo": grupo_nome,
            "message": msg,
            "app_state": get_app_state(),
        }
    except Exception as exc:
        err_msg = f"Erro ao importar dados: {exc}"
        state.add_log(f"[Importação - Erro] {err_msg}")
        return {"success": False, "error": err_msg}


# =====================================================================
# ROTAS DE GESTÃO DE CONSULTAS INDEPENDENTES (PROJETOS / BASES .DB)
# =====================================================================

@app.get("/api/consultas")
async def get_consultas_list():
    """Retorna a lista de todos os bancos .db de consultas independentes salvos em data/consultas/."""
    try:
        consultas = list_consultas()
        active_info = get_active_consulta_info()
        return {
            "success": True,
            "count": len(consultas),
            "active": active_info,
            "data": consultas,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc), "data": []}


@app.get("/api/consultas/ativa")
async def get_active_consulta():
    """Retorna as informações da consulta ativa no momento."""
    try:
        active_info = get_active_consulta_info()
        return {"success": True, "data": active_info}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@app.post("/api/consultas/ativar")
async def activate_consulta(req: SetActiveConsultaRequest):
    """Define uma consulta específica como o banco de dados ativo para análise e chat."""
    filename = req.filename.strip()
    if not filename:
        return {"success": False, "message": "Nome do arquivo da consulta não informado."}
    try:
        res = set_active_consulta(filename)
        state.add_log(f"[Consulta] 📂 Consulta '{filename}' ativada com sucesso! Grupo: {res.get('active_group_name', 'Geral')} ({res.get('total_messages', 0)} mensagens).")
        return {"success": True, "data": res, "message": f"Consulta '{filename}' ativada com sucesso."}
    except Exception as exc:
        err = f"Falha ao ativar consulta: {exc}"
        state.add_log(f"[Consulta - Erro] {err}")
        return {"success": False, "error": err}


@app.delete("/api/consultas/{filename}")
async def delete_consulta_endpoint(filename: str):
    """Exclui com segurança um arquivo de consulta independente."""
    clean_name = os.path.basename(filename)
    removido = delete_consulta(clean_name)
    if removido:
        state.add_log(f"[Consulta] 🗑️ Consulta '{clean_name}' excluída com sucesso.")
        return {"success": True, "message": f"Consulta '{clean_name}' removida com sucesso."}
    else:
        return {"success": False, "message": f"Consulta '{clean_name}' não encontrada para exclusão."}


@app.get("/api/consultas/download/{filename}")
async def download_consulta_file(filename: str):
    """Permite o download direto do arquivo de banco SQLite (.db) de uma consulta independente."""
    clean_name = os.path.basename(filename)
    consultas_dir = get_consultas_dir()
    target_file = consultas_dir / clean_name
    if not target_file.exists():
        legacy_file = get_data_dir() / clean_name
        if legacy_file.exists():
            target_file = legacy_file
        else:
            raise HTTPException(status_code=404, detail="Arquivo de consulta não encontrado.")

    state.add_log(f"[Consulta] Download do banco de dados '{clean_name}' iniciado.")
    return FileResponse(
        path=str(target_file),
        filename=clean_name,
        media_type="application/octet-stream",
    )


@app.post("/api/consultas/abrir-pasta")
async def open_consultas_folder():
    """Abre a pasta data/consultas/ no explorador de arquivos do Windows."""
    consultas_dir = get_consultas_dir()
    try:
        if sys.platform == "win32":
            os.startfile(str(consultas_dir))
        elif sys.platform == "darwin":
            subprocess.run(["open", str(consultas_dir)], check=True)
        else:
            subprocess.run(["xdg-open", str(consultas_dir)], check=True)
        state.add_log(f"[Consulta] Pasta de consultas aberta: {consultas_dir}")
        return {"success": True, "path": str(consultas_dir)}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# =====================================================================
# ROTAS DA NOVA ABA DE HISTÓRICO & GERENCIAMENTO DE BACKUPS
# =====================================================================

@app.get("/api/historico/stats")
async def get_historico_stats():
    """Retorna os KPIs gerais de persistência, histórico e espaço ocupado."""
    try:
        stats = get_persistence_stats(get_db_path())
        return {"success": True, "data": stats}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@app.get("/api/historico/coletas")
async def get_historico_coletas(limit: int = 100):
    """Retorna a lista cronológica de coletas e consultas realizadas."""
    try:
        coletas = list_coletas_historico(limit=limit, db_path=get_db_path())
        return {"success": True, "count": len(coletas), "data": coletas}
    except Exception as exc:
        return {"success": False, "error": str(exc), "data": []}


@app.get("/api/historico/grupos")
async def get_historico_grupos():
    """Retorna os grupos persistidos no banco SQLite com suas respectivas métricas."""
    try:
        grupos = list_grupos_historico(get_db_path())
        return {"success": True, "count": len(grupos), "data": grupos}
    except Exception as exc:
        return {"success": False, "error": str(exc), "data": []}


@app.delete("/api/historico/grupos/{identifier:path}")
async def delete_historico_grupo(identifier: str):
    """Remove todas as mensagens e coletas de um grupo específico do banco messages.db."""
    clean_id = identifier.strip()
    if not clean_id:
        return {"success": False, "message": "Identificador do grupo não informado."}
    try:
        total = delete_group_messages(clean_id, db_path=get_db_path())
        state.add_log(f"[Histórico] 🗑️ {total} mensagens do grupo '{clean_id}' excluídas do banco ativo.")
        return {
            "success": True,
            "deleted_messages": total,
            "message": f"{total} mensagens do grupo '{clean_id}' foram excluídas com sucesso.",
            "app_state": get_app_state(),
        }
    except Exception as exc:
        err = f"Falha ao excluir mensagens do grupo: {exc}"
        state.add_log(f"[Histórico - Erro] {err}")
        return {"success": False, "error": err}


@app.get("/api/backups")
async def get_backups_list():
    """Retorna a lista de todos os snapshots de backup salvos em data/backups/."""
    try:
        backups = list_backups()
        return {"success": True, "count": len(backups), "data": backups}
    except Exception as exc:
        return {"success": False, "error": str(exc), "data": []}


@app.post("/api/backups/criar")
async def create_backup_endpoint(req: CreateBackupRequest):
    """Cria um novo backup manual (snapshot completo do banco messages.db)."""
    if state.is_busy:
        return {"success": False, "message": "Operação em andamento. Aguarde antes de gerar um backup."}

    try:
        tag = req.tag.strip() or "manual"
        desc = req.description.strip() or "Backup manual gerado pelo usuário"
        meta = create_backup(tag=tag, description=desc, db_path=get_db_path())
        state.add_log(f"[Backup] ✅ Snapshot '{meta['filename']}' ({meta['size_formatted']}) criado com sucesso!")
        return {
            "success": True,
            "backup": meta,
            "message": f"Backup '{meta['filename']}' gerado com sucesso ({meta['total_messages']} mensagens em {meta['total_grupos']} grupos).",
        }
    except Exception as exc:
        err = f"Falha ao criar backup: {exc}"
        state.add_log(f"[Backup - Erro] {err}")
        return {"success": False, "error": err}


@app.post("/api/backups/restaurar")
async def restore_backup_endpoint(req: RestoreBackupRequest):
    """Restaura o banco messages.db a partir de um snapshot selecionado em data/backups/."""
    if state.is_busy:
        return {"success": False, "message": "Operação em andamento. Aguarde antes de restaurar o backup."}

    filename = req.filename.strip()
    if not filename:
        return {"success": False, "message": "Nome do arquivo de backup não informado."}

    try:
        state.add_log(f"[Backup] Iniciando restauração do snapshot '{filename}'...")
        res = restore_backup(filename=filename, db_path=get_db_path())
        state.add_log(f"[Backup] ✅ {res['message']}")
        return res
    except Exception as exc:
        err = f"Erro ao restaurar backup: {exc}"
        state.add_log(f"[Backup - Erro] {err}")
        return {"success": False, "error": err}


@app.delete("/api/backups/{filename}")
async def delete_backup_endpoint(filename: str):
    """Remove um arquivo de backup do disco."""
    clean_name = os.path.basename(filename)
    removido = delete_backup(clean_name)
    if removido:
        state.add_log(f"[Backup] Snapshot '{clean_name}' excluído.")
        return {"success": True, "message": f"Backup '{clean_name}' removido."}
    else:
        return {"success": False, "message": f"Backup '{clean_name}' não encontrado para exclusão."}


@app.get("/api/backups/download/{filename}")
async def download_backup_file(filename: str):
    """Permite o download direto do arquivo de backup SQLite (.db)."""
    clean_name = os.path.basename(filename)
    backups_dir = get_backups_dir()
    target_file = backups_dir / clean_name
    if not target_file.exists():
        raise HTTPException(status_code=404, detail="Arquivo de backup não encontrado.")

    state.add_log(f"[Backup] Download do arquivo '{clean_name}' iniciado.")
    return FileResponse(
        path=str(target_file),
        filename=clean_name,
        media_type="application/octet-stream",
    )


@app.post("/api/backups/abrir-pasta")
async def abrir_pasta_backups():
    """Abre a pasta data/backups/ no gerenciador de arquivos do sistema operacional."""
    backups_dir = get_backups_dir()
    backups_dir.mkdir(parents=True, exist_ok=True)
    pasta_str = str(backups_dir)

    try:
        if sys.platform == "win32":
            os.startfile(pasta_str)
        elif sys.platform == "darwin":
            subprocess.run(["open", pasta_str])
        else:
            subprocess.run(["xdg-open", pasta_str])
        state.add_log(f"Pasta de backups aberta: {pasta_str}")
        return {"success": True, "message": f"Pasta aberta: {pasta_str}"}
    except Exception as exc:
        state.add_log(f"Erro ao abrir pasta de backups: {exc}")
        return {"success": False, "error": str(exc)}


@app.get("/api/session/status")
async def get_session_status():
    has_session = verificar_status_sessao()
    return {
        "has_session": has_session,
        "session_status": "connected" if has_session else "disconnected",
        "is_busy": state.is_busy,
    }


def _run_coletor_login_thread():
    state.is_busy = True
    orig_stdout = sys.stdout
    redirector = StdoutRedirector(orig_stdout)
    sys.stdout = redirector

    try:
        state.add_log("Iniciando conector para autenticação no WhatsApp Web...")
        sucesso = iniciar_coletor(timeout_segundos=300)
        if sucesso:
            state.add_log("Sessão conectada e salva com sucesso!")
        else:
            state.add_log("Conexão finalizada sem autenticação.")
    except Exception as exc:
        state.add_log(f"Erro ao conectar sessão: {exc}")
    finally:
        sys.stdout = orig_stdout
        state.is_busy = False


@app.post("/api/session/conectar")
async def trigger_conectar_sessao(background_tasks: BackgroundTasks):
    if state.is_busy:
        return {"success": False, "message": "Já existe uma tarefa em execução."}

    background_tasks.add_task(_run_coletor_login_thread)
    return {"success": True, "message": "Navegador aberto para escanear o QR Code."}


@app.post("/api/session/desconectar")
async def trigger_desconectar_sessao():
    if state.is_busy:
        return {"success": False, "message": "Não é possível desconectar durante uma coleta ou operação em andamento."}

    sucesso, msg = desconectar_sessao()
    if sucesso:
        state.add_log(f"[Sessão] {msg}")
        return {"success": True, "message": msg}
    else:
        state.add_log(f"[Sessão - Erro] {msg}")
        return {"success": False, "error": msg}


@app.post("/api/sistema/limpar-tudo")
async def trigger_limpar_tudo():
    """
    Limpa completamente os dados da aplicação (pasta data/ e pasta sessao_whatsapp/)
    para permitir ao usuário recomeçar do zero absoluto.
    """
    if state.is_busy:
        return {"success": False, "message": "Não é possível limpar os dados durante uma coleta ou operação em andamento."}

    # 1. Desconecta e limpa o perfil de sessão do WhatsApp
    sucesso_sessao, msg_sessao = desconectar_sessao()

    # 2. Limpa todos os arquivos da pasta data/ e recria o banco vazio
    sucesso_data, msg_data = wipe_all_data()

    if sucesso_data and sucesso_sessao:
        state.add_log("[Sistema] Limpeza total concluída: mensagens, catálogos e sessão foram redefinidos.")
        return {
            "success": True,
            "message": "Todos os dados locais e a sessão do WhatsApp foram removidos com sucesso. A aplicação foi resetada para o estado inicial.",
            "status": {
                "has_session": False,
                "message_count": 0,
                "active_group": None,
                "app_state": get_app_state(),
            },
        }
    else:
        err = f"Falhas durante a limpeza: {msg_sessao} | {msg_data}"
        state.add_log(f"[Sistema - Erro] {err}")
        return {"success": False, "error": err}


@app.get("/api/grupos")
async def get_grupos():
    try:
        details = load_catalog_groups()
        nomes = get_catalog_group_names()
        return {
            "success": True,
            "count": len(details),
            "data": nomes,
            "details": details,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc), "data": [], "details": []}


@app.delete("/api/grupos/{identifier:path}")
async def delete_grupo_catalog(identifier: str):
    """Exclui um grupo individual do arquivo data/groups_catalog.json."""
    clean_id = identifier.strip()
    if not clean_id:
        return {"success": False, "message": "Identificador do grupo não informado."}
    try:
        removido = delete_catalog_group(clean_id)
        if removido:
            state.add_log(f"[Catálogo] 🗑️ Grupo '{clean_id}' excluído do catálogo com sucesso.")
            return {
                "success": True,
                "message": f"Grupo '{clean_id}' excluído do catálogo.",
                "count": len(load_catalog_groups()),
            }
        else:
            return {"success": False, "message": f"Grupo '{clean_id}' não encontrado no catálogo."}
    except Exception as exc:
        err = f"Falha ao excluir grupo do catálogo: {exc}"
        state.add_log(f"[Catálogo - Erro] {err}")
        return {"success": False, "error": err}


@app.post("/api/grupos/excluir-lote")
async def delete_grupos_catalog_batch(req: DeleteCatalogGroupsBatchRequest):
    """Exclui múltiplos grupos selecionados do arquivo data/groups_catalog.json em lote."""
    if not req.group_ids:
        return {"success": False, "message": "Nenhum grupo informado para exclusão em lote."}
    try:
        removidos = delete_catalog_groups(req.group_ids)
        state.add_log(f"[Catálogo] 🗑️ {removidos} grupos removidos do catálogo em lote.")
        return {
            "success": True,
            "removed_count": removidos,
            "message": f"{removidos} grupo(s) removido(s) do catálogo com sucesso.",
            "count": len(load_catalog_groups()),
        }
    except Exception as exc:
        err = f"Falha ao excluir grupos em lote do catálogo: {exc}"
        state.add_log(f"[Catálogo - Erro] {err}")
        return {"success": False, "message": err, "error": err}


def _run_sincronizar_grupos_thread():
    state.is_busy = True
    orig_stdout = sys.stdout
    redirector = StdoutRedirector(orig_stdout)
    sys.stdout = redirector

    try:
        state.add_log("Iniciando varredura no WhatsApp Web para catalogar grupos...")
        grupos = sincronizar_grupos_whatsapp(headless=False, timeout_segundos=60)
        if grupos:
            state.add_log(f"[Sincronização] {len(grupos)} grupos catalogados com sucesso!")
        else:
            state.add_log("[Sincronização] Nenhum grupo encontrado ou sessão desconectada.")
    except Exception as exc:
        state.add_log(f"Erro ao sincronizar grupos: {exc}")
    finally:
        sys.stdout = orig_stdout
        state.is_busy = False


@app.post("/api/grupos/sincronizar")
async def trigger_sincronizar_grupos(background_tasks: BackgroundTasks):
    if state.is_busy:
        return {"success": False, "message": "Já existe uma tarefa em execução."}
    if not verificar_status_sessao():
        return {"success": False, "message": "Sessão do WhatsApp não está autenticada. Conecte a sessão primeiro."}

    background_tasks.add_task(_run_sincronizar_grupos_thread)
    return {"success": True, "message": "Sincronização de grupos iniciada em segundo plano."}


@app.get("/api/messages")
async def get_messages(
    limit: int = 250,
    grupo_id: str | None = None,
    grupo_nome: str | None = None,
    apenas_ativo: bool = False,
):
    try:
        init_db(get_db_path())
        if apenas_ativo and not grupo_id and not grupo_nome:
            app_state = get_app_state()
            grupo_id = app_state.get("active_group_id")
            grupo_nome = app_state.get("active_group_name")

        messages = fetch_recent(limit=limit, db_path=get_db_path(), grupo_id=grupo_id, grupo_nome=grupo_nome)
        return {"success": True, "count": len(messages), "data": messages}
    except Exception as exc:
        return {"success": False, "error": str(exc), "data": []}


@app.get("/api/messages/{message_id}")
async def get_message_detail(message_id: str):
    data = fetch_message_by_id(message_id, db_path=get_db_path())
    if not data:
        raise HTTPException(status_code=404, detail="Mensagem não encontrada.")
    return {"success": True, "data": data}


def _run_coleta_thread(req: ColetaRequest):
    state.is_busy = True
    grupo_solicitado = req.grupo.strip() if req.grupo else ""
    if grupo_solicitado:
        grupo = grupo_solicitado
    else:
        active_group = detect_active_group_from_db(get_db_path())
        if active_group and (active_group.get("nome") or active_group.get("id")):
            grupo = active_group.get("nome") or active_group.get("id")
        else:
            grupo = NOME_DO_GRUPO
    unidade_tempo = req.unidade_tempo.lower().strip() if req.unidade_tempo else "dias"
    valor = int(req.valor) if req.valor else 7

    kwargs = {
        "nome_grupo": grupo,
        "unidade_tempo": unidade_tempo,
        "valor": valor,
        "tipo_filtro": req.tipo_filtro,
    }

    state.add_log(f"Iniciando coleta por janela de {valor} {unidade_tempo} (Grupo: '{grupo}')...")

    orig_stdout = sys.stdout
    redirector = StdoutRedirector(orig_stdout)
    sys.stdout = redirector

    try:
        extrair_dados_comunidade(**kwargs)
        state.add_log("Coleta concluída com sucesso!")
    except Exception as exc:
        state.add_log(f"Erro durante a coleta: {exc}")
    finally:
        sys.stdout = orig_stdout
        state.is_busy = False


@app.post("/api/coletar")
async def trigger_coleta(req: ColetaRequest, background_tasks: BackgroundTasks):
    if state.is_busy:
        return {"success": False, "message": "Já existe uma coleta em andamento."}

    background_tasks.add_task(_run_coleta_thread, req)
    return {"success": True, "message": "Coleta iniciada em segundo plano."}


@app.get("/api/logs")
async def get_logs():
    with state.lock:
        return {"logs": list(state.log_history), "is_busy": state.is_busy}


@app.get("/api/logs/stream")
async def stream_logs() -> StreamingResponse:
    async def event_generator() -> AsyncGenerator[str, None]:
        q = state.subscribe()
        try:
            with state.lock:
                for line in state.log_history[-50:]:
                    yield f"data: {json.dumps({'message': line, 'is_busy': state.is_busy})}\n\n"

            while True:
                try:
                    msg = await asyncio.to_thread(q.get, timeout=1.0)
                    yield f"data: {json.dumps({'message': msg, 'is_busy': state.is_busy})}\n\n"
                except queue.Empty:
                    yield f"data: {json.dumps({'ping': True, 'is_busy': state.is_busy})}\n\n"
        finally:
            state.unsubscribe(q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/exportar")
async def export_csv(req: ExportRequest):
    destino = req.destino.strip() or str(DEFAULT_EXPORT_PATH)
    pasta = os.path.dirname(destino)
    if pasta:
        os.makedirs(pasta, exist_ok=True)

    try:
        export_to_csv(
            destino,
            db_path=get_db_path(),
            grupo_id=req.grupo_id,
            grupo_nome=req.grupo_nome,
        )
        state.add_log(f"CSV exportado com sucesso em: {destino}")
        return {"success": True, "path": destino, "message": f"Arquivo salvo em: {destino}"}
    except Exception as exc:
        state.add_log(f"Erro ao exportar CSV: {exc}")
        return {"success": False, "error": str(exc)}


@app.get("/api/exportar/download")
async def download_csv(grupo_id: str | None = None, grupo_nome: str | None = None):
    destino = str(DEFAULT_EXPORT_PATH)
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    try:
        count = export_to_csv(
            destino,
            db_path=get_db_path(),
            grupo_id=grupo_id,
            grupo_nome=grupo_nome,
        )
        app_state = get_app_state()
        nome_base = app_state.get("active_group_name") or grupo_nome or "mensagens"
        texto_norm = unicodedata.normalize("NFKD", str(nome_base)).encode("ascii", "ignore").decode("ascii")
        slug = re.sub(r"[^\w\s-]", "", texto_norm).strip().lower()
        slug = re.sub(r"[-\s]+", "_", slug) or "mensagens"
        filename = f"export_{slug}.csv"
        state.add_log(f"[Exportação] CSV gerado para download com sucesso ({count} mensagens): {filename}")
        return FileResponse(
            path=destino,
            filename=filename,
            media_type="text/csv; charset=utf-8",
        )
    except Exception as exc:
        state.add_log(f"[Exportação - Erro] Falha ao gerar CSV: {exc}")
        raise HTTPException(status_code=500, detail=f"Erro ao gerar CSV: {exc}")


@app.get("/api/exportar/json")
async def download_json(grupo_id: str | None = None, grupo_nome: str | None = None):
    json_path = DATA_DIR / "export_messages.json"
    os.makedirs(os.path.dirname(str(json_path)), exist_ok=True)
    try:
        count = export_to_json(
            str(json_path),
            db_path=get_db_path(),
            grupo_id=grupo_id,
            grupo_nome=grupo_nome,
        )
        app_state = get_app_state()
        nome_base = app_state.get("active_group_name") or grupo_nome or "mensagens"
        texto_norm = unicodedata.normalize("NFKD", str(nome_base)).encode("ascii", "ignore").decode("ascii")
        slug = re.sub(r"[^\w\s-]", "", texto_norm).strip().lower()
        slug = re.sub(r"[-\s]+", "_", slug) or "mensagens"
        filename = f"export_{slug}.json"
        state.add_log(f"[Exportação] JSON gerado para download com sucesso ({count} mensagens): {filename}")
        return FileResponse(
            path=str(json_path),
            filename=filename,
            media_type="application/json; charset=utf-8",
        )
    except Exception as exc:
        state.add_log(f"[Exportação - Erro] Falha ao gerar JSON: {exc}")
        raise HTTPException(status_code=500, detail=f"Erro ao gerar JSON: {exc}")


@app.post("/api/abrir-pasta-db")
async def abrir_pasta_db():
    db_path = get_db_path()
    pasta = os.path.dirname(db_path)
    os.makedirs(pasta, exist_ok=True)

    try:
        if sys.platform == "win32":
            os.startfile(pasta)
        elif sys.platform == "darwin":
            subprocess.run(["open", pasta])
        else:
            subprocess.run(["xdg-open", pasta])
        state.add_log(f"Pasta do banco aberta: {pasta}")
        return {"success": True, "message": f"Pasta aberta: {pasta}"}
    except Exception as exc:
        state.add_log(f"Erro ao abrir pasta: {exc}")
        return {"success": False, "error": str(exc)}


@app.post("/api/abrir-banco")
async def abrir_banco():
    db_path = get_db_path()
    try:
        if os.path.exists(db_path):
            if sys.platform == "win32":
                os.startfile(db_path)
            elif sys.platform == "darwin":
                subprocess.run(["open", db_path])
            else:
                subprocess.run(["xdg-open", db_path])
            state.add_log(f"Banco aberto: {db_path}")
            return {"success": True, "message": f"Banco aberto: {db_path}"}
        else:
            return await abrir_pasta_db()
    except Exception as exc:
        state.add_log(f"Erro ao abrir banco: {exc}")
        return {"success": False, "error": str(exc)}


@app.get("/api/llm/modelos")
async def get_llm_modelos():
    """Retorna os modelos de LLM suportados (Anthropic Claude)."""
    return {
        "success": True,
        "provedor": "Anthropic (Claude)",
        "filosofia": "Chave de API / Configuração Local",
        "modelos": AnthropicService.listar_modelos(),
    }


@app.post("/api/llm/validar")
async def validar_chave_llm(req: ValidateLLMKeyRequest):
    """Valida a API Key informada pelo usuário executando um teste contra a Anthropic."""
    api_key = req.api_key.strip() or os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return {"success": False, "message": "Nenhuma API Key informada. Digite sua chave da Anthropic (iniciada com 'sk-ant-')."}

    modelo = req.model.strip() or "claude-3-5-sonnet-20241022"
    service = AnthropicService(api_key=api_key, model=modelo)

    state.add_log(f"[Anthropic / Claude] Validando API Key informada para o modelo '{modelo}'...")
    valida, msg, modelos_disp, modelo_resolvido = service.validar_api_key(api_key=api_key, model=modelo)

    if valida:
        state.add_log(f"[Anthropic / Claude] ✅ {msg}")
        return {"success": True, "message": msg, "model": modelo_resolvido, "available_models": modelos_disp}
    else:
        state.add_log(f"[Anthropic / Claude] ⚠️ {msg}")
        return {"success": False, "message": msg, "model": modelo_resolvido, "available_models": modelos_disp}


@app.get("/api/llm/range-datas")
async def get_llm_date_range():
    """Retorna os limites cronológicos das mensagens do banco local para alimentar o slider duplo."""
    db_path = get_db_path()
    bounds = get_message_date_bounds(db_path)
    active_grp = detect_active_group_from_db(db_path)
    if active_grp:
        bounds["grupo_nome"] = active_grp.get("nome") or active_grp.get("id") or bounds.get("grupo_nome")
    return {"success": True, "data": bounds}


@app.get("/api/llm/analises-uteis")
async def get_llm_analises_uteis():
    """Retorna a lista estruturada de análises úteis pré-programadas."""
    return {
        "success": True,
        "data": AnthropicService.listar_analises_uteis(),
    }


@app.post("/api/llm/chat")
async def processar_chat_llm(req: ChatLLMRequest):
    """
    Executa a inferência de inteligência artificial via Anthropic (Claude) unindo a solicitação
    do usuário às mensagens do banco SQLite dentro do intervalo de tempo selecionado.
    """
    api_key = req.api_key.strip() or os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return {"success": False, "message": "API Key da Anthropic não fornecida. Configure sua chave no card superior."}

    modelo = req.model.strip() or "claude-3-5-sonnet-20241022"
    db_path = get_db_path()

    # Busca o grupo ativo
    active_grp = detect_active_group_from_db(db_path)
    grupo_nome = (active_grp.get("nome") or active_grp.get("id")) if active_grp else "Comunidade WhatsApp"

    # Recupera as mensagens do período no SQLite
    mensagens = fetch_messages_for_llm_range(
        start_ts=req.start_ts,
        end_ts=req.end_ts,
        limit=1500,
        db_path=db_path,
    )

    if not mensagens:
        return {
            "success": False,
            "message": "Nenhuma mensagem encontrada no período selecionado. Ajuste os limites do slider de datas para incluir mensagens.",
        }

    desc_analise = f" ({req.tipo_analise})" if req.tipo_analise else ""
    state.add_log(f"[Anthropic / Claude] Processando consulta{desc_analise} com {len(mensagens)} mensagens do grupo '{grupo_nome}' via modelo '{modelo}'...")

    service = AnthropicService(api_key=api_key, model=modelo)
    sucesso, resposta = service.gerar_insights_chat(
        prompt_usuario=req.prompt,
        mensagens=mensagens,
        historico=req.historico,
        tipo_analise=req.tipo_analise,
        grupo_nome=grupo_nome,
    )

    if sucesso:
        state.add_log(f"[Anthropic / Claude] ✅ Resposta gerada com sucesso ({len(resposta)} caracteres).")
        return {
            "success": True,
            "response": resposta,
            "total_messages": len(mensagens),
            "grupo": grupo_nome,
            "model": modelo,
        }
    else:
        state.add_log(f"[Anthropic / Claude] ⚠️ Falha na geração: {resposta}")
        return {
            "success": False,
            "message": resposta,
            "total_messages": len(mensagens),
            "grupo": grupo_nome,
            "model": modelo,
        }


@app.get("/api/relatorios/meses")
async def get_report_months():
    """Retorna os meses disponíveis na base de dados para alimentar o seletor de relatórios."""
    db_path = get_db_path()
    meses = ReportService.listar_meses_disponiveis(db_path)
    return {"success": True, "data": meses}


@app.get("/api/relatorios/dados")
async def get_report_data(mes: str | None = None):
    """Retorna as métricas completas calculadas para o relatório mensal nas 4 seções."""
    db_path = get_db_path()
    dados = ReportService.calcular_metricas_mensais(mes=mes, db_path=db_path)
    return {"success": True, "data": dados}


@app.post("/api/relatorios/gerar-resumo")
async def generate_report_summary(req: ReportSummaryRequest):
    """Gera um Resumo Executivo inteligente e consolidado via Anthropic (Claude)."""
    api_key = req.api_key.strip() or os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return {"success": False, "message": "API Key da Anthropic não fornecida. Configure sua chave no card de configuração."}

    db_path = get_db_path()
    metricas = ReportService.calcular_metricas_mensais(mes=req.mes, db_path=db_path)
    if not metricas.get("tem_dados"):
        return {"success": False, "message": "Não há mensagens suficientes no mês selecionado para gerar o resumo executivo."}

    modelo = req.model.strip() or "claude-3-5-sonnet-20241022"
    state.add_log(f"[Relatórios / Anthropic] Gerando Resumo Executivo Mensal ({req.mes}) via modelo '{modelo}'...")
    sucesso, texto = ReportService.gerar_resumo_executivo_llm(
        api_key=api_key,
        model=modelo,
        mes=req.mes,
        metricas=metricas,
        db_path=db_path,
    )

    if sucesso:
        state.add_log(f"[Relatórios / Anthropic] ✅ Resumo Executivo gerado com sucesso ({len(texto)} caracteres).")
        return {"success": True, "resumo": texto}
    else:
        state.add_log(f"[Relatórios / Anthropic] ⚠️ Falha na geração do resumo: {texto}")
        return {"success": False, "message": texto}


def start_server(host: str = "127.0.0.1", port: int = 8000):
    print("\n" + "=" * 55)
    print("WHATSAPP INSIGHTS - SERVIDOR WEB ATIVO")
    print("=" * 55)
    print(f"-> Local:    http://{host}:{port}")
    print(f"-> Hostname: http://localhost:{port}")
    print("=" * 55 + "\n")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    start_server()
