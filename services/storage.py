import os
import sqlite3
import json
from datetime import datetime


from services.paths import get_data_dir, get_db_path


def init_db(db_path: str | None = None):
    """Inicializa o banco SQLite com tabelas para mensagens híbridas."""
    if db_path is None:
        db_path = get_db_path()

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            coleta_id TEXT,
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

    # Migração para tabelas já existentes sem os campos de coleta
    cols = [row[1] for row in cur.execute("PRAGMA table_info(messages)").fetchall()]
    for column_name, column_type in {
        "coleta_id": "TEXT",
        "grupo_nome": "TEXT",
        "comunidade_nome": "TEXT",
        "coletado_em": "TEXT",
        "meses_back": "INTEGER",
        "semanas_back": "INTEGER",
    }.items():
        if column_name not in cols:
            cur.execute(f"ALTER TABLE messages ADD COLUMN {column_name} {column_type}")

    conn.commit()
    # Índices para consultas rápidas
    cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_data_hora_ts ON messages(data_hora_ts);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_remetente ON messages(remetente);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_has_attachments ON messages(has_attachments);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_coleta_id ON messages(coleta_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_grupo_nome ON messages(grupo_nome);")
    conn.close()


def _serialize_msg(m: dict) -> dict:
    # converte campos para tipos serializáveis
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

    return {
        "id": m.get("id"),
        "coleta_id": m.get("coleta_id"),
        "grupo_nome": m.get("grupo_nome"),
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
        "created_at": datetime.utcnow().isoformat()
    }


def save_messages(messages: list[dict], db_path: str | None = None):
    """Salva uma lista de mensagens no banco. Faz upsert por `id`."""
    if db_path is None:
        db_path = get_db_path()

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    for m in messages:
        s = _serialize_msg(m)
        cur.execute(
            """
            INSERT INTO messages (
                id, coleta_id, grupo_nome, comunidade_nome, coletado_em, meses_back, semanas_back,
                data_hora, data_hora_ts, remetente, texto, texto_normalizado,
                is_reply, reply_author, reply_text, has_attachments, attachments_json,
                reactions_json, topics_json, transcript, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                coleta_id = excluded.coleta_id,
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
                s["id"], s["coleta_id"], s["grupo_nome"], s["comunidade_nome"], s["coletado_em"], s["meses_back"], s["semanas_back"],
                s["data_hora"], s["data_hora_ts"], s["remetente"], s["texto"], s["texto_normalizado"],
                s["is_reply"], s["reply_author"], s["reply_text"], s["has_attachments"], s["attachments_json"],
                s["reactions_json"], s["topics_json"], s["transcript"], s["created_at"]
            ),
        )

    conn.commit()
    conn.close()


def fetch_recent(limit: int = 100, db_path: str | None = None) -> list[dict]:
    if db_path is None:
        db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT id, data_hora, remetente, texto, has_attachments FROM messages ORDER BY data_hora_ts DESC LIMIT ?", (limit,))
    rows = cur.fetchall()
    conn.close()
    return [
        {"id": r[0], "data_hora": r[1], "remetente": r[2], "texto": r[3], "has_attachments": bool(r[4])}
        for r in rows
    ]


def fetch_message_by_id(message_id: str, db_path: str | None = None) -> dict | None:
    if db_path is None:
        db_path = get_db_path()

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, coleta_id, grupo_nome, comunidade_nome, coletado_em, meses_back, semanas_back,
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
        "id", "coleta_id", "grupo_nome", "comunidade_nome", "coletado_em", "meses_back", "semanas_back",
        "data_hora", "data_hora_ts", "remetente", "texto", "texto_normalizado",
        "is_reply", "reply_author", "reply_text", "has_attachments", "attachments_json",
        "reactions_json", "topics_json", "transcript", "created_at",
    ]
    data = dict(zip(cols, row))
    data["has_attachments"] = bool(data.get("has_attachments"))
    return data


def export_to_csv(path: str, limit: int | None = None, db_path: str | None = None):
    """Exporta mensagens para CSV (UTF-8)."""
    import csv

    if db_path is None:
        db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    q = "SELECT id, coleta_id, grupo_nome, comunidade_nome, coletado_em, meses_back, semanas_back, data_hora, remetente, texto, texto_normalizado, is_reply, reply_author, reply_text, has_attachments, attachments_json, reactions_json, topics_json, transcript, created_at FROM messages"
    params = []
    if limit is not None:
        q += " ORDER BY data_hora_ts DESC LIMIT ?"
        params.append(limit)

    cur.execute(q, params)
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    conn.close()

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter=';')
        writer.writerow(cols)
        for r in rows:
            writer.writerow(r)


def export_to_json(path: str, limit: int | None = None, db_path: str | None = None):
    """Exporta mensagens para JSON (lista de objetos)."""
    if db_path is None:
        db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    q = "SELECT id, coleta_id, grupo_nome, comunidade_nome, coletado_em, meses_back, semanas_back, data_hora, remetente, texto, texto_normalizado, is_reply, reply_author, reply_text, has_attachments, attachments_json, reactions_json, topics_json, transcript, created_at FROM messages"
    params = []
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
