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

from services.extrator import NOME_DO_GRUPO, extrair_dados_comunidade
from services.storage import (
    export_to_csv,
    fetch_message_by_id,
    fetch_recent,
    get_db_path,
    init_db,
)

BASE_DIR = Path(__file__).resolve().parent.parent
UI_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = UI_DIR / "templates"
DATA_DIR = BASE_DIR / "data"
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
    comunidade: str = ""
    tipo_filtro: str = "mes"  # 'mes' ou 'dias'
    valor: int = 1


class ExportRequest(BaseModel):
    destino: str = str(DEFAULT_EXPORT_PATH)


@app.on_event("startup")
def startup_event():
    init_db(get_db_path())
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
    return {
        "status": "online",
        "is_busy": state.is_busy,
        "db_path": db_path,
        "db_exists": os.path.exists(db_path),
        "default_grupo": NOME_DO_GRUPO,
        "default_export_path": str(DEFAULT_EXPORT_PATH),
    }


@app.get("/api/messages")
async def get_messages(limit: int = 100):
    try:
        init_db(get_db_path())
        messages = fetch_recent(limit=limit, db_path=get_db_path())
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
    comunidade = req.comunidade.strip() or ""
    tipo_filtro = req.tipo_filtro if req.tipo_filtro in ["mes", "dias"] else "mes"
    valor = int(req.valor)

    kwargs = {
        "nome_grupo": grupo,
        "nome_comunidade": comunidade,
        "tipo_filtro": tipo_filtro,
    }
    if tipo_filtro == "mes":
        kwargs["meses"] = valor
    else:
        kwargs["dias"] = valor

    state.add_log(f"Iniciando coleta por {tipo_filtro} com valor {valor} (Grupo: '{grupo}')...")

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
        export_to_csv(destino, db_path=get_db_path())
        state.add_log(f"CSV exportado com sucesso em: {destino}")
        return {"success": True, "path": destino, "message": f"Arquivo salvo em: {destino}"}
    except Exception as exc:
        state.add_log(f"Erro ao exportar CSV: {exc}")
        return {"success": False, "error": str(exc)}


@app.get("/api/exportar/download")
async def download_csv():
    destino = str(DEFAULT_EXPORT_PATH)
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    try:
        export_to_csv(destino, db_path=get_db_path())
        return FileResponse(
            path=destino,
            filename="export_messages.csv",
            media_type="text/csv",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erro ao gerar CSV: {exc}")


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
