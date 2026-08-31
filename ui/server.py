from __future__ import annotations

import asyncio
import io
import json
import os
import queue
import subprocess
import sys
import threading
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
    export_to_csv,
    export_to_json,
    fetch_known_groups,
    fetch_known_groups_details,
    fetch_message_by_id,
    fetch_recent,
    get_app_state,
    import_from_csv_data,
    import_from_json_data,
    init_db,
    save_known_groups,
    set_active_group,
)
from services.paths import get_base_dir, get_data_dir, get_db_path, get_templates_dir

BASE_DIR = get_base_dir()
TEMPLATES_DIR = get_templates_dir()
DATA_DIR = get_data_dir()
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


class ExportRequest(BaseModel):
    destino: str = str(DEFAULT_EXPORT_PATH)
    grupo_id: str | None = None
    grupo_nome: str | None = None


class ImportRequest(BaseModel):
    content: str
    format: str = "csv"  # 'csv' ou 'json'
    filename: str | None = None


@app.on_event("startup")
def startup_event():
    init_db(get_db_path())
    app_state = get_app_state()
    grp = app_state.get("active_group_name")
    if grp:
        state.add_log(f"Interface inicializada. Grupo ativo na sessão: '{grp}'.")
    else:
        state.add_log("Interface Web inicializada com sucesso.")


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
    app_state = get_app_state()
    total_messages = count_messages(db_path)

    active_group = None
    if app_state.get("active_group_name") or app_state.get("active_group_id"):
        grp_nome = app_state.get("active_group_name")
        grp_id = app_state.get("active_group_id")
        grp_count = count_messages(db_path, grupo_id=grp_id, grupo_nome=grp_nome)
        active_group = {
            "id": grp_id,
            "nome": grp_nome,
            "total_messages": grp_count,
        }

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

    new_state = set_active_group(group_id=req.grupo_id, group_name=req.grupo_nome)
    state.add_log(f"[Grupo] Grupo ativo definido: '{req.grupo_nome or req.grupo_id}'.")
    return {"success": True, "state": new_state}


@app.post("/api/state/clear-group")
async def clear_group_endpoint():
    """Limpa o grupo ativo da memória (sem apagar as mensagens do banco)."""
    new_state = clear_active_group()
    state.add_log("[Grupo] Grupo ativo limpo da memória. Pronto para nova seleção/extração.")
    return {"success": True, "state": new_state, "message": "Grupo desmarcado da memória."}


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

    try:
        if fmt == "json":
            count, grupo_nome = import_from_json_data(content, db_path=get_db_path())
        else:
            count, grupo_nome = import_from_csv_data(content, db_path=get_db_path())

        nome_arq = f" '{req.filename}'" if req.filename else ""
        msg = f"Importação concluída com sucesso! {count} mensagens processadas a partir de{nome_arq}."
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


@app.get("/api/grupos")
async def get_grupos():
    try:
        details = fetch_known_groups_details(get_db_path())
        nomes = [d["nome"] for d in details if d.get("nome")]
        return {
            "success": True,
            "count": len(details),
            "data": nomes,
            "details": details,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc), "data": [], "details": []}


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
    grupo = req.grupo.strip() or NOME_DO_GRUPO
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
        export_to_csv(
            destino,
            db_path=get_db_path(),
            grupo_id=grupo_id,
            grupo_nome=grupo_nome,
        )
        return FileResponse(
            path=destino,
            filename="export_messages.csv",
            media_type="text/csv",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erro ao gerar CSV: {exc}")


@app.get("/api/exportar/json")
async def download_json(grupo_id: str | None = None, grupo_nome: str | None = None):
    json_path = DATA_DIR / "export_messages.json"
    os.makedirs(os.path.dirname(str(json_path)), exist_ok=True)
    try:
        export_to_json(
            str(json_path),
            db_path=get_db_path(),
            grupo_id=grupo_id,
            grupo_nome=grupo_nome,
        )
        return FileResponse(
            path=str(json_path),
            filename="export_messages.json",
            media_type="application/json",
        )
    except Exception as exc:
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
