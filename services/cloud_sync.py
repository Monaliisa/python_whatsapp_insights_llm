from __future__ import annotations

import json
import os
import sqlite3
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from services.database import get_db_session, is_postgres
from services.models import CatalogGroup, ColetaHistorico, Consulta, Message
from services.paths import (
    get_consultas_dir,
    get_data_dir,
    get_db_path,
    get_groups_catalog_path,
)


def is_cloud_sync_enabled() -> bool:
    """Verifica se a conexão com o PostgreSQL / Neon está ativa para sincronização."""
    return is_postgres()


def sync_catalog_groups_to_cloud(groups: list[dict | str]) -> int:
    """
    Sincroniza uma lista de grupos catalogados para a tabela catalog_groups no Neon (PostgreSQL).
    Utiliza upsert baseado na PRIMARY KEY id.
    """
    if not is_cloud_sync_enabled() or not groups:
        return 0

    now_iso = datetime.now().isoformat()
    registros: list[dict] = []

    for item in groups:
        if isinstance(item, str):
            nome = item.strip()
            comunidade = ""
            gid = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode("ascii").lower().replace(" ", "_")
        elif isinstance(item, dict):
            nome = (item.get("nome") or item.get("grupo_nome") or item.get("title") or "").strip()
            comunidade = (item.get("comunidade") or item.get("comunidade_nome") or "").strip()
            gid = item.get("id") or item.get("grupo_id") or unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode("ascii").lower().replace(" ", "_")
        else:
            continue

        if not nome or len(nome) < 2 or not gid:
            continue

        registros.append({
            "id": str(gid),
            "nome": nome,
            "comunidade": comunidade,
            "atualizado_em": item.get("atualizado_em") if isinstance(item, dict) and item.get("atualizado_em") else now_iso,
        })

    if not registros:
        return 0

    try:
        with get_db_session() as session:
            for r in registros:
                stmt = pg_insert(CatalogGroup).values(**r)
                stmt = stmt.on_conflict_do_update(
                    index_elements=[CatalogGroup.id],
                    set_={
                        "nome": stmt.excluded.nome,
                        "comunidade": stmt.excluded.comunidade,
                        "atualizado_em": stmt.excluded.atualizado_em,
                    },
                )
                session.execute(stmt)
        return len(registros)
    except Exception as e:
        print(f"[AVISO CLOUD SYNC] Erro ao sincronizar catálogo de grupos com Neon: {e}")
        return 0


def sync_consulta_to_cloud(consulta_data: dict) -> str | None:
    """
    Sincroniza uma sessão/consulta de análise para a tabela consultas no Neon.
    """
    if not is_cloud_sync_enabled() or not consulta_data:
        return None

    cid = consulta_data.get("id")
    if not cid:
        return None

    now_iso = datetime.now().isoformat()
    record = {
        "id": str(cid),
        "nome": consulta_data.get("nome") or "Consulta Sem Nome",
        "grupo_id": consulta_data.get("grupo_id"),
        "grupo_nome": consulta_data.get("grupo_nome"),
        "comunidade_nome": consulta_data.get("comunidade_nome"),
        "total_mensagens": int(consulta_data.get("total_mensagens", 0)),
        "criado_em": consulta_data.get("criado_em") or now_iso,
        "atualizado_em": consulta_data.get("atualizado_em") or now_iso,
        "status": consulta_data.get("status") or "Ativa",
        "metadata_json": consulta_data.get("metadata_json") or "{}",
    }

    try:
        with get_db_session() as session:
            stmt = pg_insert(Consulta).values(**record)
            stmt = stmt.on_conflict_do_update(
                index_elements=[Consulta.id],
                set_={
                    "nome": stmt.excluded.nome,
                    "grupo_id": stmt.excluded.grupo_id,
                    "grupo_nome": stmt.excluded.grupo_nome,
                    "comunidade_nome": stmt.excluded.comunidade_nome,
                    "total_mensagens": stmt.excluded.total_mensagens,
                    "atualizado_em": stmt.excluded.atualizado_em,
                    "status": stmt.excluded.status,
                    "metadata_json": stmt.excluded.metadata_json,
                },
            )
            session.execute(stmt)
        return cid
    except Exception as e:
        print(f"[AVISO CLOUD SYNC] Erro ao sincronizar consulta com Neon: {e}")
        return None


def sync_coleta_historico_to_cloud(coleta_data: dict, consulta_id: str | None = None) -> str | None:
    """
    Sincroniza um registro de histórico de coleta para a tabela coletas_historico no Neon.
    Garante que a consulta pai exista se consulta_id for informado.
    """
    if not is_cloud_sync_enabled() or not coleta_data:
        return None

    cid = coleta_data.get("id")
    if not cid:
        return None

    exec_em = coleta_data.get("executado_em") or datetime.now().isoformat()
    detalhes = coleta_data.get("detalhes")
    if isinstance(detalhes, dict):
        detalhes_str = json.dumps(detalhes, ensure_ascii=False)
    else:
        detalhes_str = coleta_data.get("detalhes_json") or "{}"

    c_id = consulta_id or coleta_data.get("consulta_id")

    # Se houver consulta_id, garante que a consulta exista previamente no banco
    if c_id:
        sync_consulta_to_cloud({
            "id": c_id,
            "nome": coleta_data.get("grupo_nome") or f"Consulta {c_id}",
            "grupo_id": coleta_data.get("grupo_id"),
            "grupo_nome": coleta_data.get("grupo_nome"),
            "comunidade_nome": coleta_data.get("comunidade_nome"),
            "total_mensagens": int(coleta_data.get("total_acumulado", 0)),
            "criado_em": exec_em,
            "atualizado_em": exec_em,
            "status": "Ativa",
        })

    record = {
        "id": str(cid),
        "consulta_id": str(c_id) if c_id else None,
        "grupo_id": coleta_data.get("grupo_id"),
        "grupo_nome": coleta_data.get("grupo_nome") or "",
        "comunidade_nome": coleta_data.get("comunidade_nome") or "",
        "executado_em": exec_em,
        "unidade_tempo": coleta_data.get("unidade_tempo") or "dias",
        "valor": int(coleta_data.get("valor", 7)),
        "tipo_filtro": coleta_data.get("tipo_filtro") or "",
        "total_extraido": int(coleta_data.get("total_extraido", 0)),
        "total_acumulado": int(coleta_data.get("total_acumulado", 0)),
        "status": coleta_data.get("status") or "Concluído",
        "detalhes_json": detalhes_str,
    }

    try:
        with get_db_session() as session:
            stmt = pg_insert(ColetaHistorico).values(**record)
            stmt = stmt.on_conflict_do_update(
                index_elements=[ColetaHistorico.id],
                set_={
                    "consulta_id": stmt.excluded.consulta_id,
                    "grupo_id": stmt.excluded.grupo_id,
                    "grupo_nome": stmt.excluded.grupo_nome,
                    "comunidade_nome": stmt.excluded.comunidade_nome,
                    "executado_em": stmt.excluded.executado_em,
                    "unidade_tempo": stmt.excluded.unidade_tempo,
                    "valor": stmt.excluded.valor,
                    "tipo_filtro": stmt.excluded.tipo_filtro,
                    "total_extraido": stmt.excluded.total_extraido,
                    "total_acumulado": stmt.excluded.total_acumulado,
                    "status": stmt.excluded.status,
                    "detalhes_json": stmt.excluded.detalhes_json,
                },
            )
            session.execute(stmt)
        return str(cid)
    except Exception as e:
        print(f"[AVISO CLOUD SYNC] Erro ao sincronizar histórico de coleta com Neon: {e}")
        return None


def sync_messages_to_cloud(
    messages: list[dict],
    consulta_id: str | None = None,
    batch_size: int = 200,
) -> int:
    """
    Sincroniza um lote de mensagens extraídas para a tabela messages no Neon.
    Realiza upsert em lote (chunks) para otimizar latência com PostgreSQL Serverless.
    """
    if not is_cloud_sync_enabled() or not messages:
        return 0

    now_iso = datetime.now().isoformat()
    records: list[dict] = []

    # Se fornecido consulta_id, garante a existência do pai
    if consulta_id:
        primeira_msg = messages[0] if messages else {}
        sync_consulta_to_cloud({
            "id": consulta_id,
            "nome": primeira_msg.get("grupo_nome") or f"Consulta {consulta_id}",
            "grupo_id": primeira_msg.get("grupo_id"),
            "grupo_nome": primeira_msg.get("grupo_nome"),
            "comunidade_nome": primeira_msg.get("comunidade_nome"),
            "total_mensagens": len(messages),
            "criado_em": now_iso,
            "atualizado_em": now_iso,
            "status": "Ativa",
        })

    for m in messages:
        mid = m.get("id")
        if not mid:
            continue

        texto = m.get("texto") or ""
        texto_norm = unicodedata.normalize("NFKD", texto).casefold()

        dh = m.get("data_hora")
        if isinstance(dh, datetime):
            dh_str = dh.strftime("%Y-%m-%d %H:%M:%S")
            dh_ts = dh.timestamp()
        elif isinstance(dh, str):
            dh_str = dh
            try:
                dh_ts = datetime.fromisoformat(dh).timestamp()
            except Exception:
                dh_ts = datetime.now().timestamp()
        else:
            dh_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            dh_ts = datetime.now().timestamp()

        attachments = m.get("attachments") or []
        has_attachments = 1 if attachments else 0
        att_json = json.dumps(attachments, ensure_ascii=False) if attachments else None
        reactions_json = (
            json.dumps(m.get("reactions"), ensure_ascii=False)
            if m.get("reactions")
            else None
        )
        topics_json = (
            json.dumps(m.get("topics"), ensure_ascii=False)
            if m.get("topics")
            else None
        )

        is_reply_val = 1 if m.get("is_reply") else 0
        reply_author_val = m.get("reply_author")
        reply_text_val = m.get("reply_text")
        if not reply_author_val and m.get("reply_data"):
            reply_author_val = m["reply_data"].get("autor_citado")
        if not reply_text_val and m.get("reply_data"):
            reply_text_val = m["reply_data"].get("texto_citado")

        c_id = consulta_id or m.get("consulta_id")

        records.append({
            "id": str(mid),
            "consulta_id": str(c_id) if c_id else None,
            "coleta_id": str(m.get("coleta_id")) if m.get("coleta_id") else None,
            "grupo_id": m.get("grupo_id"),
            "grupo_nome": m.get("grupo_nome") or m.get("grupo"),
            "comunidade_nome": m.get("comunidade_nome") or m.get("comunidade"),
            "coletado_em": m.get("coletado_em") or now_iso,
            "meses_back": m.get("meses_back"),
            "semanas_back": m.get("semanas_back"),
            "data_hora": dh_str,
            "data_hora_ts": dh_ts,
            "remetente": m.get("remetente"),
            "texto": texto,
            "texto_normalizado": texto_norm,
            "is_reply": is_reply_val,
            "reply_author": reply_author_val,
            "reply_text": reply_text_val,
            "has_attachments": has_attachments,
            "attachments_json": att_json,
            "reactions_json": reactions_json,
            "topics_json": topics_json,
            "transcript": m.get("transcript"),
            "created_at": m.get("created_at") or now_iso,
        })

    if not records:
        return 0

    total_synced = 0
    try:
        with get_db_session() as session:
            for i in range(0, len(records), batch_size):
                chunk = records[i:i + batch_size]
                for r in chunk:
                    stmt = pg_insert(Message).values(**r)
                    stmt = stmt.on_conflict_do_update(
                        index_elements=[Message.id],
                        set_={
                            "consulta_id": stmt.excluded.consulta_id,
                            "coleta_id": stmt.excluded.coleta_id,
                            "grupo_id": stmt.excluded.grupo_id,
                            "grupo_nome": stmt.excluded.grupo_nome,
                            "comunidade_nome": stmt.excluded.comunidade_nome,
                            "coletado_em": stmt.excluded.coletado_em,
                            "meses_back": stmt.excluded.meses_back,
                            "semanas_back": stmt.excluded.semanas_back,
                            "data_hora": stmt.excluded.data_hora,
                            "data_hora_ts": stmt.excluded.data_hora_ts,
                            "remetente": stmt.excluded.remetente,
                            "texto": stmt.excluded.texto,
                            "texto_normalizado": stmt.excluded.texto_normalizado,
                            "is_reply": stmt.excluded.is_reply,
                            "reply_author": stmt.excluded.reply_author,
                            "reply_text": stmt.excluded.reply_text,
                            "has_attachments": stmt.excluded.has_attachments,
                            "attachments_json": stmt.excluded.attachments_json,
                            "reactions_json": stmt.excluded.reactions_json,
                            "topics_json": stmt.excluded.topics_json,
                            "transcript": stmt.excluded.transcript,
                        },
                    )
                    session.execute(stmt)
                total_synced += len(chunk)
        return total_synced
    except Exception as e:
        print(f"[AVISO CLOUD SYNC] Erro ao sincronizar mensagens com Neon: {e}")
        return total_synced


def sync_all_local_data_to_cloud() -> dict:
    """
    Lê todos os bancos SQLite locais (data/messages.db, data/consultas/*.db)
    e o catálogo de grupos (data/groups_catalog.json) e sincroniza tudo para o Neon.
    Retorna estatísticas detalhadas da sincronização.
    """
    if not is_cloud_sync_enabled():
        return {
            "success": False,
            "message": "DATABASE_URL não configurada ou banco ativo não é PostgreSQL/Neon.",
            "groups_synced": 0,
            "messages_synced": 0,
            "coletas_synced": 0,
        }

    stats = {
        "groups_synced": 0,
        "consultas_synced": 0,
        "coletas_synced": 0,
        "messages_synced": 0,
    }

    # 1. Sincroniza Catálogo de Grupos
    catalog_path = get_groups_catalog_path()
    if catalog_path.exists():
        try:
            with open(catalog_path, "r", encoding="utf-8") as f:
                grupos = json.load(f)
                if isinstance(grupos, list):
                    stats["groups_synced"] = sync_catalog_groups_to_cloud(grupos)
        except Exception as e:
            print(f"[AVISO CLOUD SYNC] Falha ao ler groups_catalog.json: {e}")

    # 2. Identifica todos os bancos SQLite locais a sincronizar
    db_paths: list[Path] = []
    main_db = Path(get_db_path())
    if main_db.exists():
        db_paths.append(main_db)

    consultas_dir = get_consultas_dir()
    if consultas_dir.exists():
        for p in consultas_dir.glob("*.db"):
            if p not in db_paths:
                db_paths.append(p)

    for db_file in db_paths:
        try:
            conn = sqlite3.connect(db_file)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            consulta_id = db_file.stem
            nome_consulta = consulta_id.replace("_", " ").title()

            # Extrai mensagens do banco SQLite
            try:
                cur.execute("SELECT * FROM messages")
                msg_rows = [dict(r) for r in cur.fetchall()]
                if msg_rows:
                    synced = sync_messages_to_cloud(msg_rows, consulta_id=consulta_id)
                    stats["messages_synced"] += synced
            except sqlite3.OperationalError:
                msg_rows = []

            # Extrai coletas_historico do banco SQLite
            try:
                cur.execute("SELECT * FROM coletas_historico")
                col_rows = [dict(r) for r in cur.fetchall()]
                for c in col_rows:
                    sync_coleta_historico_to_cloud(c, consulta_id=consulta_id)
                    stats["coletas_synced"] += 1
            except sqlite3.OperationalError:
                pass

            conn.close()
            stats["consultas_synced"] += 1
        except Exception as e:
            print(f"[AVISO CLOUD SYNC] Erro ao sincronizar banco local {db_file.name}: {e}")

    return {
        "success": True,
        "message": f"Sincronização concluída com sucesso no Neon! {stats['messages_synced']} mensagens, {stats['groups_synced']} grupos e {stats['coletas_synced']} históricos replicados.",
        **stats,
    }
