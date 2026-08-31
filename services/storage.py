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

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS known_groups (
            nome TEXT PRIMARY KEY,
            comunidade_nome TEXT,
            atualizado_em TEXT
        )
        """
    )

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


def count_messages(db_path: str | None = None) -> int:
    """Retorna o total de mensagens armazenadas no banco SQLite."""
    if db_path is None:
        db_path = get_db_path()
    if not os.path.exists(db_path):
        return 0
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM messages")
        row = cur.fetchone()
        conn.close()
        return row[0] if row else 0
    except Exception:
        return 0


def import_from_csv_data(content: str, db_path: str | None = None) -> int:
    """Importa mensagens a partir do conteúdo textual de um CSV."""
    import csv
    import io
    import uuid

    if not content or not content.strip():
        return 0

    if db_path is None:
        db_path = get_db_path()
    init_db(db_path)

    linhas = content.strip().split("\n")
    if not linhas:
        return 0

    primeira_linha = linhas[0]
    delimiter = ";" if ";" in primeira_linha else ","

    reader = csv.DictReader(io.StringIO(content), delimiter=delimiter)
    messages = []
    for row in reader:
        msg_id = row.get("id") or str(uuid.uuid4())
        meses_val = row.get("meses_back")
        semanas_val = row.get("semanas_back")
        
        reply_author = row.get("reply_author")
        reply_text = row.get("reply_text")

        msg = {
            "id": msg_id,
            "coleta_id": row.get("coleta_id"),
            "grupo_nome": row.get("grupo_nome") or row.get("grupo"),
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
            } if reply_author or reply_text else None,
            "has_attachments": 1 if str(row.get("has_attachments", "")).lower() in ["1", "true", "sim"] else 0,
            "attachments": row.get("attachments_json"),
            "reactions": row.get("reactions_json"),
            "topics": row.get("topics_json"),
            "transcript": row.get("transcript"),
        }
        messages.append(msg)

    if messages:
        save_messages(messages, db_path=db_path)
    return len(messages)


def import_from_json_data(content: str, db_path: str | None = None) -> int:
    """Importa mensagens a partir do conteúdo textual de um JSON."""
    import uuid

    if not content or not content.strip():
        return 0

    if db_path is None:
        db_path = get_db_path()
    init_db(db_path)

    data = json.loads(content)
    if isinstance(data, dict):
        data = data.get("messages") or data.get("data") or [data]
    if not isinstance(data, list):
        data = [data]

    messages = []
    for item in data:
        if not isinstance(item, dict):
            continue
        msg_id = item.get("id") or str(uuid.uuid4())
        msg = {
            "id": msg_id,
            "coleta_id": item.get("coleta_id"),
            "grupo_nome": item.get("grupo_nome") or item.get("grupo"),
            "comunidade_nome": item.get("comunidade_nome") or item.get("comunidade"),
            "coletado_em": item.get("coletado_em"),
            "meses_back": item.get("meses_back"),
            "semanas_back": item.get("semanas_back"),
            "data_hora": item.get("data_hora"),
            "remetente": item.get("remetente") or item.get("autor") or item.get("sender"),
            "texto": item.get("texto") or item.get("mensagem") or item.get("content") or "",
            "texto_normalizado": item.get("texto_normalizado"),
            "is_reply": item.get("is_reply"),
            "reply_data": item.get("reply_data") or ({
                "autor_citado": item.get("reply_author"),
                "texto_citado": item.get("reply_text"),
            } if item.get("reply_author") or item.get("reply_text") else None),
            "has_attachments": item.get("has_attachments"),
            "attachments": item.get("attachments") or item.get("attachments_json"),
            "reactions": item.get("reactions") or item.get("reactions_json"),
            "topics": item.get("topics") or item.get("topics_json"),
            "transcript": item.get("transcript"),
        }
        messages.append(msg)

    if messages:
        save_messages(messages, db_path=db_path)
    return len(messages)


def save_known_groups(groups: list[dict | str], db_path: str | None = None) -> int:
    """
    Salva ou atualiza a lista de grupos conhecidos no SQLite.
    Aceita lista de strings (nomes dos grupos) ou dicts.
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
        elif isinstance(item, dict):
            nome = (item.get("nome") or item.get("grupo_nome") or "").strip()
            comunidade = (item.get("comunidade") or item.get("comunidade_nome") or "").strip()
        else:
            continue

        if not nome:
            continue

        cur.execute(
            """
            INSERT INTO known_groups (nome, comunidade_nome, atualizado_em)
            VALUES (?, ?, ?)
            ON CONFLICT(nome) DO UPDATE SET
                comunidade_nome = CASE WHEN excluded.comunidade_nome != '' THEN excluded.comunidade_nome ELSE known_groups.comunidade_nome END,
                atualizado_em = excluded.atualizado_em
            """,
            (nome, comunidade, now_str),
        )
        count += 1

    conn.commit()
    conn.close()
    return count


def fetch_known_groups(db_path: str | None = None) -> list[str]:
    """
    Retorna a lista ordenada dos nomes de todos os grupos conhecidos, unindo os grupos salvos
    na tabela `known_groups` com os nomes de grupos presentes no histórico de mensagens,
    filtrando números de telefone e chats individuais.
    """
    import re
    if db_path is None:
        db_path = get_db_path()
    init_db(db_path)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    query = """
        SELECT DISTINCT nome FROM (
            SELECT nome FROM known_groups WHERE nome IS NOT NULL AND TRIM(nome) != ''
            UNION
            SELECT grupo_nome AS nome FROM messages WHERE grupo_nome IS NOT NULL AND TRIM(grupo_nome) != ''
        ) ORDER BY nome COLLATE NOCASE ASC
    """
    rows = cur.execute(query).fetchall()
    conn.close()

    re_phone = re.compile(r"^[\+]?[\d\s\-\(\)\.]{7,}$")
    ignorar = {"você", "you", "meta ai", "whatsapp", "mensagens favoritas", "avisos", "status", "rascunhos", "drafts", "nome desconhecido"}

    grupos_validos = []
    for (nome,) in rows:
        n = (nome or "").strip()
        # Remove marcas de unicode bidi
        clean_n = re.sub(r"[\u200E\u200F\u202A-\u202E]", "", n).strip()
        digitos_puros = re.sub(r"[\s\-\(\)\+\.]", "", clean_n)
        
        if (
            clean_n
            and len(clean_n) > 1
            and not digitos_puros.isdigit()
            and not re_phone.match(clean_n)
            and clean_n.lower() not in ignorar
        ):
            grupos_validos.append(clean_n)

    return sorted(list(dict.fromkeys(grupos_validos)), key=lambda s: s.lower())


