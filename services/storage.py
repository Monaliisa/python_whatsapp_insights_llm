from __future__ import annotations

import csv
import io
import json
import os
import re
import sqlite3
import unicodedata
import uuid
from datetime import datetime
from typing import Any

from services.paths import get_data_dir, get_db_path, get_state_file_path


def gerar_grupo_id(nome_grupo: str | None, jid: str | None = None) -> str:
    """
    Gera ou formata um identificador único para o grupo.
    Prioriza o JID oficial do WhatsApp (ex: '120363024823123456@g.us').
    Se ausente, gera um slug determinístico a partir do nome do grupo.
    """
    if jid and "@g.us" in jid:
        return jid.strip()

    if not nome_grupo or not nome_grupo.strip():
        return "grupo_desconhecido"

    texto_limpo = unicodedata.normalize("NFKD", nome_grupo.strip()).encode("ASCII", "ignore").decode("ASCII")
    slug = re.sub(r"[^\w\s-]", "", texto_limpo.lower()).strip()
    slug = re.sub(r"[-\s]+", "_", slug)
    return slug or "grupo"


def init_db(db_path: str | None = None):
    """Inicializa o banco SQLite com tabelas para mensagens e grupos."""
    if db_path is None:
        db_path = get_db_path()

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            coleta_id TEXT,
            grupo_id TEXT,
            grupo_nome TEXT,
            comunidade_nome TEXT,
            coletado_em TEXT,
            meses_back INTEGER,
            semanas_back INTEGER,
            data_hora TEXT,
            data_hora_ts REAL,
            remetente TEXT,
            texto TEXT,
            texto_normalizado TEXT,
            is_reply INTEGER,
            reply_author TEXT,
            reply_text TEXT,
            has_attachments INTEGER,
            attachments_json TEXT,
            reactions_json TEXT,
            topics_json TEXT,
            transcript TEXT,
            created_at TEXT
        )
        """
    )

    # Migração para tabelas já existentes sem os novos campos
    cols_msg = [row[1] for row in cur.execute("PRAGMA table_info(messages)").fetchall()]
    for column_name, column_type in {
        "coleta_id": "TEXT",
        "grupo_id": "TEXT",
        "grupo_nome": "TEXT",
        "comunidade_nome": "TEXT",
        "coletado_em": "TEXT",
        "meses_back": "INTEGER",
        "semanas_back": "INTEGER",
    }.items():
        if column_name not in cols_msg:
            cur.execute(f"ALTER TABLE messages ADD COLUMN {column_name} {column_type}")

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS known_groups (
            nome TEXT PRIMARY KEY,
            id TEXT,
            comunidade_nome TEXT,
            atualizado_em TEXT
        )
        """
    )

    # Migração da tabela known_groups se id não existia
    cols_grp = [row[1] for row in cur.execute("PRAGMA table_info(known_groups)").fetchall()]
    if "id" not in cols_grp:
        try:
            cur.execute("ALTER TABLE known_groups ADD COLUMN id TEXT")
        except Exception:
            pass

    conn.commit()
    # Índices para consultas rápidas
    cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_data_hora_ts ON messages(data_hora_ts);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_remetente ON messages(remetente);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_has_attachments ON messages(has_attachments);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_coleta_id ON messages(coleta_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_grupo_id ON messages(grupo_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_grupo_nome ON messages(grupo_nome);")
    conn.close()


def get_app_state() -> dict:
    """
    Retorna o estado persistido da aplicação (último grupo ativo na memória resistiva).
    Se o grupo salvo possuir mensagens no banco, calcula a contagem em tempo real.
    """
    state_file = get_state_file_path()
    default_state = {
        "active_group_id": None,
        "active_group_name": None,
        "last_updated": None,
        "total_messages": 0,
    }

    if not state_file.exists():
        return default_state

    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return default_state

        grp_id = data.get("active_group_id")
        grp_nome = data.get("active_group_name")

        if grp_nome or grp_id:
            count = count_messages(grupo_id=grp_id, grupo_nome=grp_nome)
            data["total_messages"] = count

        return data
    except Exception:
        return default_state


def set_active_group(group_id: str | None = None, group_name: str | None = None) -> dict:
    """
    Persiste o grupo ativo na memória resistiva (data/app_state.json).
    """
    state_file = get_state_file_path()

    if not group_id and group_name:
        group_id = gerar_grupo_id(group_name)

    total = count_messages(grupo_id=group_id, grupo_nome=group_name)
    state = {
        "active_group_id": group_id,
        "active_group_name": group_name,
        "last_updated": datetime.now().isoformat(),
        "total_messages": total,
    }

    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        print(f"[Aviso] Falha ao gravar app_state.json: {exc}")

    return state


def clear_active_group() -> dict:
    """
    Limpa o grupo ativo da memória da aplicação sem excluir nenhuma mensagem do banco de dados.
    """
    state_file = get_state_file_path()
    state = {
        "active_group_id": None,
        "active_group_name": None,
        "last_updated": datetime.now().isoformat(),
        "total_messages": 0,
    }
    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        print(f"[Aviso] Falha ao limpar app_state.json: {exc}")
    return state


def _serialize_msg(m: dict) -> dict:
    # Converte campos para tipos serializáveis
    data_hora = m.get("data_hora")
    if hasattr(data_hora, "isoformat"):
        data_hora_str = data_hora.isoformat()
        try:
            data_hora_ts = data_hora.timestamp()
        except Exception:
            data_hora_ts = None
    else:
        data_hora_str = data_hora or None
        data_hora_ts = None

    attachments = m.get("attachments")
    attachments_json = json.dumps(attachments, ensure_ascii=False) if attachments is not None else None

    reactions = m.get("reactions") or m.get("reactions_json")
    reactions_json = json.dumps(reactions, ensure_ascii=False) if reactions is not None else None

    topics = m.get("topics") or m.get("topics_json") or m.get("insights")
    topics_json = json.dumps(topics, ensure_ascii=False) if topics is not None else None

    grupo_nome = m.get("grupo_nome") or m.get("grupo")
    grupo_id = m.get("grupo_id") or gerar_grupo_id(grupo_nome, jid=m.get("chat_jid"))

    return {
        "id": m.get("id"),
        "coleta_id": m.get("coleta_id"),
        "grupo_id": grupo_id,
        "grupo_nome": grupo_nome,
        "comunidade_nome": m.get("comunidade_nome"),
        "coletado_em": m.get("coletado_em"),
        "meses_back": m.get("meses_back"),
        "semanas_back": m.get("semanas_back"),
        "data_hora": data_hora_str,
        "data_hora_ts": data_hora_ts,
        "remetente": m.get("remetente"),
        "texto": m.get("texto"),
        "texto_normalizado": m.get("texto_normalizado"),
        "is_reply": 1 if m.get("is_reply") else 0,
        "reply_author": (m.get("reply_data") or {}).get("autor_citado") if m.get("reply_data") else None,
        "reply_text": (m.get("reply_data") or {}).get("texto_citado") if m.get("reply_data") else None,
        "has_attachments": 1 if m.get("has_attachments") else 0,
        "attachments_json": attachments_json,
        "reactions_json": reactions_json,
        "topics_json": topics_json,
        "transcript": m.get("transcript") or None,
        "created_at": datetime.utcnow().isoformat(),
    }


def save_messages(messages: list[dict], db_path: str | None = None):
    """Salva uma lista de mensagens no banco SQLite com upsert por `id`."""
    if db_path is None:
        db_path = get_db_path()
    init_db(db_path)

    if not messages:
        return

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    grupos_para_salvar = {}

    for m in messages:
        s = _serialize_msg(m)
        cur.execute(
            """
            INSERT INTO messages (
                id, coleta_id, grupo_id, grupo_nome, comunidade_nome, coletado_em, meses_back, semanas_back,
                data_hora, data_hora_ts, remetente, texto, texto_normalizado,
                is_reply, reply_author, reply_text, has_attachments, attachments_json,
                reactions_json, topics_json, transcript, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                coleta_id = excluded.coleta_id,
                grupo_id = excluded.grupo_id,
                grupo_nome = excluded.grupo_nome,
                comunidade_nome = excluded.comunidade_nome,
                coletado_em = excluded.coletado_em,
                meses_back = excluded.meses_back,
                semanas_back = excluded.semanas_back,
                data_hora = excluded.data_hora,
                data_hora_ts = excluded.data_hora_ts,
                remetente = excluded.remetente,
                texto = excluded.texto,
                texto_normalizado = excluded.texto_normalizado,
                is_reply = excluded.is_reply,
                reply_author = excluded.reply_author,
                reply_text = excluded.reply_text,
                has_attachments = excluded.has_attachments,
                attachments_json = excluded.attachments_json,
                reactions_json = excluded.reactions_json,
                topics_json = excluded.topics_json,
                transcript = excluded.transcript,
                created_at = excluded.created_at
            """,
            (
                s["id"],
                s["coleta_id"],
                s["grupo_id"],
                s["grupo_nome"],
                s["comunidade_nome"],
                s["coletado_em"],
                s["meses_back"],
                s["semanas_back"],
                s["data_hora"],
                s["data_hora_ts"],
                s["remetente"],
                s["texto"],
                s["texto_normalizado"],
                s["is_reply"],
                s["reply_author"],
                s["reply_text"],
                s["has_attachments"],
                s["attachments_json"],
                s["reactions_json"],
                s["topics_json"],
                s["transcript"],
                s["created_at"],
            ),
        )

        if s["grupo_nome"]:
            grupos_para_salvar[s["grupo_id"]] = {
                "id": s["grupo_id"],
                "nome": s["grupo_nome"],
                "comunidade": s["comunidade_nome"],
            }

    conn.commit()
    conn.close()

    if grupos_para_salvar:
        save_known_groups(list(grupos_para_salvar.values()), db_path=db_path)


def fetch_recent(
    limit: int = 100,
    db_path: str | None = None,
    grupo_id: str | None = None,
    grupo_nome: str | None = None,
) -> list[dict]:
    """Retorna as mensagens mais recentes, opcionalmente filtradas por grupo."""
    if db_path is None:
        db_path = get_db_path()
    init_db(db_path)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    query = "SELECT id, data_hora, remetente, texto, has_attachments, grupo_id, grupo_nome FROM messages"
    params: list[Any] = []

    if grupo_id or grupo_nome:
        filtros = []
        if grupo_id:
            filtros.append("grupo_id = ?")
            params.append(grupo_id)
        if grupo_nome:
            filtros.append("grupo_nome = ?")
            params.append(grupo_nome)
        query += f" WHERE ({' OR '.join(filtros)})"

    query += " ORDER BY data_hora_ts DESC LIMIT ?"
    params.append(limit)

    cur.execute(query, params)
    rows = cur.fetchall()
    conn.close()
    return [
        {
            "id": r[0],
            "data_hora": r[1],
            "remetente": r[2],
            "texto": r[3],
            "has_attachments": bool(r[4]),
            "grupo_id": r[5],
            "grupo_nome": r[6],
        }
        for r in rows
    ]


def fetch_message_by_id(message_id: str, db_path: str | None = None) -> dict | None:
    if db_path is None:
        db_path = get_db_path()
    init_db(db_path)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, coleta_id, grupo_id, grupo_nome, comunidade_nome, coletado_em, meses_back, semanas_back,
               data_hora, data_hora_ts, remetente, texto, texto_normalizado,
               is_reply, reply_author, reply_text, has_attachments, attachments_json,
               reactions_json, topics_json, transcript, created_at
        FROM messages WHERE id = ? LIMIT 1
        """,
        (message_id,),
    )
    row = cur.fetchone()
    conn.close()

    if row is None:
        return None

    cols = [
        "id",
        "coleta_id",
        "grupo_id",
        "grupo_nome",
        "comunidade_nome",
        "coletado_em",
        "meses_back",
        "semanas_back",
        "data_hora",
        "data_hora_ts",
        "remetente",
        "texto",
        "texto_normalizado",
        "is_reply",
        "reply_author",
        "reply_text",
        "has_attachments",
        "attachments_json",
        "reactions_json",
        "topics_json",
        "transcript",
        "created_at",
    ]
    data = dict(zip(cols, row))
    data["has_attachments"] = bool(data.get("has_attachments"))
    return data


def export_to_csv(
    path: str,
    limit: int | None = None,
    db_path: str | None = None,
    grupo_id: str | None = None,
    grupo_nome: str | None = None,
):
    """Exporta mensagens para CSV (UTF-8 com separador ';')."""
    if db_path is None:
        db_path = get_db_path()
    init_db(db_path)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    q = "SELECT id, coleta_id, grupo_id, grupo_nome, comunidade_nome, coletado_em, meses_back, semanas_back, data_hora, remetente, texto, texto_normalizado, is_reply, reply_author, reply_text, has_attachments, attachments_json, reactions_json, topics_json, transcript, created_at FROM messages"
    params: list[Any] = []

    if grupo_id or grupo_nome:
        filtros = []
        if grupo_id:
            filtros.append("grupo_id = ?")
            params.append(grupo_id)
        if grupo_nome:
            filtros.append("grupo_nome = ?")
            params.append(grupo_nome)
        q += f" WHERE ({' OR '.join(filtros)})"

    if limit is not None:
        q += " ORDER BY data_hora_ts DESC LIMIT ?"
        params.append(limit)

    cur.execute(q, params)
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    conn.close()

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(cols)
        for r in rows:
            writer.writerow(r)


def export_to_json(
    path: str,
    limit: int | None = None,
    db_path: str | None = None,
    grupo_id: str | None = None,
    grupo_nome: str | None = None,
):
    """Exporta mensagens para JSON (lista de objetos UTF-8)."""
    if db_path is None:
        db_path = get_db_path()
    init_db(db_path)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    q = "SELECT id, coleta_id, grupo_id, grupo_nome, comunidade_nome, coletado_em, meses_back, semanas_back, data_hora, remetente, texto, texto_normalizado, is_reply, reply_author, reply_text, has_attachments, attachments_json, reactions_json, topics_json, transcript, created_at FROM messages"
    params: list[Any] = []

    if grupo_id or grupo_nome:
        filtros = []
        if grupo_id:
            filtros.append("grupo_id = ?")
            params.append(grupo_id)
        if grupo_nome:
            filtros.append("grupo_nome = ?")
            params.append(grupo_nome)
        q += f" WHERE ({' OR '.join(filtros)})"

    if limit is not None:
        q += " ORDER BY data_hora_ts DESC LIMIT ?"
        params.append(limit)

    cur.execute(q, params)
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    conn.close()

    data = [dict(zip(cols, r)) for r in rows]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def count_messages(
    db_path: str | None = None,
    grupo_id: str | None = None,
    grupo_nome: str | None = None,
) -> int:
    """Retorna o total de mensagens armazenadas no SQLite, com filtro opcional por grupo."""
    if db_path is None:
        db_path = get_db_path()
    if not os.path.exists(db_path):
        return 0
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        if grupo_id or grupo_nome:
            filtros = []
            params: list[Any] = []
            if grupo_id:
                filtros.append("grupo_id = ?")
                params.append(grupo_id)
            if grupo_nome:
                filtros.append("grupo_nome = ?")
                params.append(grupo_nome)
            cur.execute(f"SELECT COUNT(*) FROM messages WHERE {' OR '.join(filtros)}", params)
        else:
            cur.execute("SELECT COUNT(*) FROM messages")
        row = cur.fetchone()
        conn.close()
        return row[0] if row else 0
    except Exception:
        return 0


def import_from_csv_data(content: str, db_path: str | None = None) -> tuple[int, str | None]:
    """
    Importa mensagens a partir do conteúdo textual de um CSV.
    Retorna (quantidade_importada, nome_do_grupo_identificado).
    """
    if not content or not content.strip():
        return 0, None

    if db_path is None:
        db_path = get_db_path()
    init_db(db_path)

    linhas = content.strip().split("\n")
    if not linhas:
        return 0, None

    primeira_linha = linhas[0]
    delimiter = ";" if ";" in primeira_linha else ","

    reader = csv.DictReader(io.StringIO(content), delimiter=delimiter)
    messages = []
    grupo_identificado = None
    grupo_id_identificado = None

    for row in reader:
        msg_id = row.get("id") or str(uuid.uuid4())
        meses_val = row.get("meses_back")
        semanas_val = row.get("semanas_back")

        reply_author = row.get("reply_author")
        reply_text = row.get("reply_text")

        grp_nome = row.get("grupo_nome") or row.get("grupo") or ""
        grp_id = row.get("grupo_id") or gerar_grupo_id(grp_nome)

        if grp_nome and not grupo_identificado:
            grupo_identificado = grp_nome
            grupo_id_identificado = grp_id

        msg = {
            "id": msg_id,
            "coleta_id": row.get("coleta_id"),
            "grupo_id": grp_id,
            "grupo_nome": grp_nome,
            "comunidade_nome": row.get("comunidade_nome") or row.get("comunidade"),
            "coletado_em": row.get("coletado_em"),
            "meses_back": int(meses_val) if meses_val and str(meses_val).isdigit() else None,
            "semanas_back": int(semanas_val) if semanas_val and str(semanas_val).isdigit() else None,
            "data_hora": row.get("data_hora"),
            "remetente": row.get("remetente") or row.get("autor") or row.get("sender"),
            "texto": row.get("texto") or row.get("mensagem") or row.get("content") or "",
            "texto_normalizado": row.get("texto_normalizado"),
            "is_reply": 1 if str(row.get("is_reply", "")).lower() in ["1", "true", "sim"] else 0,
            "reply_data": {
                "autor_citado": reply_author,
                "texto_citado": reply_text,
            }
            if reply_author or reply_text
            else None,
            "has_attachments": 1 if str(row.get("has_attachments", "")).lower() in ["1", "true", "sim"] else 0,
            "attachments": row.get("attachments_json"),
            "reactions": row.get("reactions_json"),
            "topics": row.get("topics_json"),
            "transcript": row.get("transcript"),
        }
        messages.append(msg)

    if messages:
        save_messages(messages, db_path=db_path)
        if grupo_identificado:
            set_active_group(grupo_id_identificado, grupo_identificado)

    return len(messages), grupo_identificado


def import_from_json_data(content: str, db_path: str | None = None) -> tuple[int, str | None]:
    """
    Importa mensagens a partir do conteúdo textual de um JSON.
    Retorna (quantidade_importada, nome_do_grupo_identificado).
    """
    if not content or not content.strip():
        return 0, None

    if db_path is None:
        db_path = get_db_path()
    init_db(db_path)

    data = json.loads(content)
    if isinstance(data, dict):
        data = data.get("messages") or data.get("data") or [data]
    if not isinstance(data, list):
        data = [data]

    messages = []
    grupo_identificado = None
    grupo_id_identificado = None

    for item in data:
        if not isinstance(item, dict):
            continue
        msg_id = item.get("id") or str(uuid.uuid4())
        grp_nome = item.get("grupo_nome") or item.get("grupo") or ""
        grp_id = item.get("grupo_id") or gerar_grupo_id(grp_nome)

        if grp_nome and not grupo_identificado:
            grupo_identificado = grp_nome
            grupo_id_identificado = grp_id

        msg = {
            "id": msg_id,
            "coleta_id": item.get("coleta_id"),
            "grupo_id": grp_id,
            "grupo_nome": grp_nome,
            "comunidade_nome": item.get("comunidade_nome") or item.get("comunidade"),
            "coletado_em": item.get("coletado_em"),
            "meses_back": item.get("meses_back"),
            "semanas_back": item.get("semanas_back"),
            "data_hora": item.get("data_hora"),
            "remetente": item.get("remetente") or item.get("autor") or item.get("sender"),
            "texto": item.get("texto") or item.get("mensagem") or item.get("content") or "",
            "texto_normalizado": item.get("texto_normalizado"),
            "is_reply": item.get("is_reply"),
            "reply_data": item.get("reply_data")
            or (
                {
                    "autor_citado": item.get("reply_author"),
                    "texto_citado": item.get("reply_text"),
                }
                if item.get("reply_author") or item.get("reply_text")
                else None
            ),
            "has_attachments": item.get("has_attachments"),
            "attachments": item.get("attachments") or item.get("attachments_json"),
            "reactions": item.get("reactions") or item.get("reactions_json"),
            "topics": item.get("topics") or item.get("topics_json"),
            "transcript": item.get("transcript"),
        }
        messages.append(msg)

    if messages:
        save_messages(messages, db_path=db_path)
        if grupo_identificado:
            set_active_group(grupo_id_identificado, grupo_identificado)

    return len(messages), grupo_identificado


def save_known_groups(groups: list[dict | str], db_path: str | None = None) -> int:
    """
    Salva ou atualiza a lista de grupos conhecidos no SQLite com identificador único.
    """
    if db_path is None:
        db_path = get_db_path()
    init_db(db_path)

    if not groups:
        return 0

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    now_str = datetime.now().isoformat()

    count = 0
    for item in groups:
        if isinstance(item, str):
            nome = item.strip()
            comunidade = ""
            gid = gerar_grupo_id(nome)
        elif isinstance(item, dict):
            nome = (item.get("nome") or item.get("grupo_nome") or item.get("title") or "").strip()
            comunidade = (item.get("comunidade") or item.get("comunidade_nome") or "").strip()
            gid = item.get("id") or item.get("grupo_id") or gerar_grupo_id(nome, jid=item.get("jid"))
        else:
            continue

        if not nome:
            continue

        cur.execute(
            """
            INSERT INTO known_groups (id, nome, comunidade_nome, atualizado_em)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(nome) DO UPDATE SET
                id = excluded.id,
                comunidade_nome = CASE WHEN excluded.comunidade_nome != '' THEN excluded.comunidade_nome ELSE known_groups.comunidade_nome END,
                atualizado_em = excluded.atualizado_em
            """,
            (gid, nome, comunidade, now_str),
        )
        count += 1

    conn.commit()
    conn.close()
    return count


def fetch_known_groups(db_path: str | None = None) -> list[str]:
    """
    Retorna a lista ordenada dos nomes de todos os grupos conhecidos.
    """
    details = fetch_known_groups_details(db_path=db_path)
    return [d["nome"] for d in details if d.get("nome")]


def fetch_known_groups_details(db_path: str | None = None) -> list[dict]:
    """
    Retorna a lista de grupos conhecidos com IDs e contagem de mensagens.
    """
    if db_path is None:
        db_path = get_db_path()
    init_db(db_path)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    query = """
        SELECT DISTINCT coalesce(kg.id, m.grupo_id, '') AS gid,
                        coalesce(kg.nome, m.grupo_nome, '') AS gname,
                        coalesce(kg.comunidade_nome, m.comunidade_nome, '') AS gcom,
                        (SELECT COUNT(*) FROM messages WHERE grupo_nome = coalesce(kg.nome, m.grupo_nome) OR grupo_id = coalesce(kg.id, m.grupo_id)) AS total_msgs
        FROM (
            SELECT id, nome, comunidade_nome FROM known_groups WHERE nome IS NOT NULL AND TRIM(nome) != ''
            UNION
            SELECT grupo_id AS id, grupo_nome AS nome, comunidade_nome FROM messages WHERE grupo_nome IS NOT NULL AND TRIM(grupo_nome) != ''
        ) kg
        LEFT JOIN messages m ON m.grupo_nome = kg.nome
        WHERE gname IS NOT NULL AND TRIM(gname) != ''
        GROUP BY gname
        ORDER BY gname COLLATE NOCASE ASC
    """
    rows = cur.execute(query).fetchall()
    conn.close()

    re_phone = re.compile(r"^[\+]?[\d\s\-\(\)\.]{7,}$")
    ignorar = {
        "você",
        "you",
        "meta ai",
        "whatsapp",
        "mensagens favoritas",
        "avisos",
        "status",
        "rascunhos",
        "drafts",
        "nome desconhecido",
    }

    grupos_validos = []
    vistos = set()

    for gid, nome, com, total_msgs in rows:
        n = (nome or "").strip()
        clean_n = re.sub(r"[\u200E\u200F\u202A-\u202E]", "", n).strip()
        digitos_puros = re.sub(r"[\s\-\(\)\+\.]", "", clean_n)

        if (
            clean_n
            and len(clean_n) > 1
            and not digitos_puros.isdigit()
            and not re_phone.match(clean_n)
            and clean_n.lower() not in ignorar
            and clean_n.lower() not in vistos
        ):
            vistos.add(clean_n.lower())
            final_gid = gid or gerar_grupo_id(clean_n)
            grupos_validos.append(
                {
                    "id": final_gid,
                    "nome": clean_n,
                    "comunidade": com or "",
                    "total_mensagens": total_msgs or 0,
                }
            )

    return sorted(grupos_validos, key=lambda g: g["nome"].lower())


