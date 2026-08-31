from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

from services.paths import (
    get_db_path,
    get_groups_catalog_path,
    get_state_file_path,
)


# =====================================================================
# 1. UTILITÁRIOS DE IDENTIFICAÇÃO DE GRUPO
# =====================================================================

def gerar_grupo_id(nome_grupo: str | None, jid: str | None = None) -> str:
    """
    Gera um identificador único para o grupo.
    - Se houver JID nativo do WhatsApp (ex: '120363024823123456@g.us'), usa o JID.
    - Caso contrário, cria um slug determinístico ASCII normalizado.
    """
    if jid and "@g.us" in jid:
        return jid.strip()

    if not nome_grupo:
        return "grupo_desconhecido"

    # Remove acentuação e caracteres especiais
    texto_norm = unicodedata.normalize("NFKD", nome_grupo)
    texto_ascii = texto_norm.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^\w\s-]", "", texto_ascii).strip().lower()
    slug = re.sub(r"[-\s]+", "_", slug)
    return slug or "grupo_whatsapp"


# =====================================================================
# 2. ESTADO DA APLICAÇÃO (data/app_state.json)
# =====================================================================

def get_app_state() -> dict:
    """
    Lê o estado persistente da aplicação a partir de data/app_state.json.
    """
    state_path = get_state_file_path()
    if not state_path.exists():
        return {
            "active_group_id": None,
            "active_group_name": None,
            "last_updated": None,
            "total_messages": 0,
        }

    try:
        with open(state_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return {
                "active_group_id": data.get("active_group_id"),
                "active_group_name": data.get("active_group_name"),
                "last_updated": data.get("last_updated"),
                "total_messages": int(data.get("total_messages", 0)),
            }
    except Exception:
        return {
            "active_group_id": None,
            "active_group_name": None,
            "last_updated": None,
            "total_messages": 0,
        }


def set_active_group(
    group_id: str | None,
    group_name: str | None,
    total_messages: int = 0,
) -> dict:
    """
    Salva na memória persistente qual o grupo ativo atual na sessão.
    """
    state_path = get_state_file_path()
    state = {
        "active_group_id": group_id,
        "active_group_name": group_name,
        "last_updated": datetime.now().isoformat(),
        "total_messages": total_messages,
    }
    try:
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[AVISO] Não foi possível gravar app_state.json: {e}")
    return state


def clear_active_group() -> dict:
    """
    Limpa o grupo ativo do app_state.json.
    """
    return set_active_group(None, None, 0)


# =====================================================================
# 3. CATÁLOGO DE GRUPOS (data/groups_catalog.json)
# =====================================================================

def save_catalog_groups(groups: list[dict | str]) -> int:
    """
    Salva uma lista de grupos descobertos no arquivo data/groups_catalog.json.
    Filtra estritamente números de telefone, chats individuais e termos de sistema.
    """
    catalog_path = get_groups_catalog_path()
    existentes = load_catalog_groups()
    mapa_existentes = {g["nome"].lower(): g for g in existentes}

    re_phone = re.compile(r"^[\+]?[\d\s\-\(\)\.]{7,}$")
    ignorar = {
        "você", "you", "meta ai", "whatsapp", "mensagens favoritas",
        "avisos", "status", "rascunhos", "drafts", "nome desconhecido",
    }

    novos_salvos = 0
    now_iso = datetime.now().isoformat()

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

        clean_n = re.sub(r"[\u200E\u200F\u202A-\u202E]", "", nome).strip()
        digitos_puros = re.sub(r"[\s\-\(\)\+\.]", "", clean_n)

        # Filtros de validação
        if not clean_n or len(clean_n) < 2:
            continue
        if digitos_puros.isdigit():
            continue
        if re_phone.match(clean_n):
            continue
        if clean_n.lower() in ignorar:
            continue

        chave = clean_n.lower()
        mapa_existentes[chave] = {
            "id": gid,
            "nome": clean_n,
            "comunidade": comunidade,
            "atualizado_em": now_iso,
        }
        novos_salvos += 1

    lista_ordenada = sorted(mapa_existentes.values(), key=lambda g: g["nome"].lower())

    try:
        with open(catalog_path, "w", encoding="utf-8") as f:
            json.dump(lista_ordenada, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[ERRO] Falha ao salvar groups_catalog.json: {e}")

    return novos_salvos


def load_catalog_groups() -> list[dict]:
    """
    Retorna a lista estruturada de grupos do arquivo data/groups_catalog.json.
    """
    catalog_path = get_groups_catalog_path()
    if not catalog_path.exists():
        return []

    try:
        with open(catalog_path, "r", encoding="utf-8") as f:
            dados = json.load(f)
            if isinstance(dados, list):
                return dados
            return []
    except Exception:
        return []


def get_catalog_group_names() -> list[str]:
    """
    Retorna apenas os nomes de todos os grupos catalogados para o dropdown.
    """
    grupos = load_catalog_groups()
    return [g["nome"] for g in grupos if g.get("nome")]


# =====================================================================
# 4. BANCO DE DADOS DE MENSAGENS (data/messages.db) - GRUPO ATIVO
# =====================================================================

def init_db(db_path: str | None = None) -> None:
    """
    Inicializa o banco SQLite messages.db.
    Garante que o banco armazena EXCLUSIVAMENTE a tabela messages do grupo ativo.
    """
    if db_path is None:
        db_path = get_db_path()

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # 1. Remove qualquer resquício de tabela de contatos/conhecidos antiga
    cur.execute("DROP TABLE IF EXISTS known_groups")

    # 2. Criação da tabela messages dedicada ao grupo
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

    # Migrações caso colunas novas faltem
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

    # Índices essenciais
    cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_data_hora_ts ON messages(data_hora_ts);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_remetente ON messages(remetente);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_has_attachments ON messages(has_attachments);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_grupo_id ON messages(grupo_id);")

    conn.commit()
    conn.close()


def detect_active_group_from_db(db_path: str | None = None) -> dict | None:
    """
    Inspeciona o messages.db. Se houver mensagens, detecta qual grupo está armazenado,
    atualiza o app_state.json e retorna os dados do grupo ativo.
    """
    if db_path is None:
        db_path = get_db_path()
    init_db(db_path)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    row = cur.execute(
        """
        SELECT grupo_id, grupo_nome, COUNT(*)
        FROM messages
        WHERE (grupo_nome IS NOT NULL AND TRIM(grupo_nome) != '')
           OR (grupo_id IS NOT NULL AND TRIM(grupo_id) != '')
        GROUP BY grupo_id, grupo_nome
        ORDER BY COUNT(*) DESC
        LIMIT 1
        """
    ).fetchone()

    total_msgs = cur.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    conn.close()

    if total_msgs > 0:
        gid = row[0] if row else None
        gnome = row[1] if row else None
        if not gnome and not gid:
            gnome = "Grupo Importado"
            gid = "grupo_importado"

        # Sincroniza app_state.json
        state = set_active_group(gid, gnome, total_msgs)
        return {
            "id": gid,
            "nome": gnome,
            "total_messages": total_msgs,
        }
    else:
        clear_active_group()
        return None


def reset_messages_db(db_path: str | None = None) -> None:
    """
    Limpa completamente as mensagens do banco de dados local para troca de grupo.
    Também limpa o estado ativo da aplicação.
    """
    if db_path is None:
        db_path = get_db_path()
    init_db(db_path)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("DELETE FROM messages")
    conn.commit()
    conn.close()

    clear_active_group()


def save_messages(messages: list[dict], db_path: str | None = None) -> int:
    """
    Salva ou atualiza mensagens extraídas na tabela messages do SQLite.
    Deduplicação garantida pela PRIMARY KEY id.
    Atualiza automaticamente o app_state.json com o grupo ativo.
    """
    if not messages:
        return 0

    if db_path is None:
        db_path = get_db_path()
    init_db(db_path)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    count = 0
    now_iso = datetime.now().isoformat()
    grupo_detectado_nome = None
    grupo_detectado_id = None

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

        nome_grp = (m.get("grupo_nome") or m.get("grupo") or "").strip()
        id_grp = m.get("grupo_id") or (gerar_grupo_id(nome_grp) if nome_grp else None)

        if nome_grp and not grupo_detectado_nome:
            grupo_detectado_nome = nome_grp
            grupo_detectado_id = id_grp

        cur.execute(
            """
            INSERT INTO messages (
                id, coleta_id, grupo_id, grupo_nome, comunidade_nome, coletado_em,
                meses_back, semanas_back, data_hora, data_hora_ts, remetente,
                texto, texto_normalizado, is_reply, reply_author, reply_text,
                has_attachments, attachments_json, reactions_json, topics_json,
                transcript, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                coleta_id = coalesce(excluded.coleta_id, messages.coleta_id),
                grupo_id = coalesce(excluded.grupo_id, messages.grupo_id),
                grupo_nome = coalesce(excluded.grupo_nome, messages.grupo_nome),
                comunidade_nome = coalesce(excluded.comunidade_nome, messages.comunidade_nome),
                coletado_em = coalesce(excluded.coletado_em, messages.coletado_em),
                meses_back = coalesce(excluded.meses_back, messages.meses_back),
                semanas_back = coalesce(excluded.semanas_back, messages.semanas_back),
                data_hora = excluded.data_hora,
                data_hora_ts = excluded.data_hora_ts,
                remetente = excluded.remetente,
                texto = excluded.texto,
                texto_normalizado = excluded.texto_normalizado,
                is_reply = excluded.is_reply,
                reply_author = excluded.reply_author,
                reply_text = excluded.reply_text,
                has_attachments = excluded.has_attachments,
                attachments_json = coalesce(excluded.attachments_json, messages.attachments_json),
                reactions_json = coalesce(excluded.reactions_json, messages.reactions_json),
                topics_json = coalesce(excluded.topics_json, messages.topics_json),
                transcript = coalesce(excluded.transcript, messages.transcript)
            """,
            (
                mid,
                m.get("coleta_id"),
                id_grp,
                nome_grp,
                m.get("comunidade_nome") or m.get("comunidade"),
                m.get("coletado_em"),
                m.get("meses_back"),
                m.get("semanas_back"),
                dh_str,
                dh_ts,
                m.get("remetente"),
                texto,
                texto_norm,
                1 if m.get("is_reply") else 0,
                m.get("reply_author"),
                m.get("reply_text"),
                has_attachments,
                att_json,
                reactions_json,
                topics_json,
                m.get("transcript"),
                now_iso,
            ),
        )
        count += 1

    total_no_banco = cur.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    conn.commit()
    conn.close()

    # Atualiza grupo ativo e catálogo
    if grupo_detectado_nome:
        set_active_group(grupo_detectado_id, grupo_detectado_nome, total_no_banco)
        save_catalog_groups([{"id": grupo_detectado_id, "nome": grupo_detectado_nome}])

    return count


def count_messages(
    db_path: str | None = None,
    grupo_id: str | None = None,
    grupo_nome: str | None = None,
) -> int:
    """Retorna o total de mensagens no banco de dados local."""
    if db_path is None:
        db_path = get_db_path()
    init_db(db_path)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    query = "SELECT COUNT(*) FROM messages"
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

    cur.execute(query, params)
    cnt = cur.fetchone()[0]
    conn.close()
    return cnt


def fetch_recent(
    limit: int = 100,
    db_path: str | None = None,
    grupo_id: str | None = None,
    grupo_nome: str | None = None,
) -> list[dict]:
    """Retorna as mensagens mais recentes do grupo ativo."""
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
    """Busca os detalhes completos de uma mensagem pelo seu ID único."""
    if db_path is None:
        db_path = get_db_path()
    init_db(db_path)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, coleta_id, grupo_id, grupo_nome, comunidade_nome, coletado_em,
               meses_back, semanas_back, data_hora, data_hora_ts, remetente,
               texto, texto_normalizado, is_reply, reply_author, reply_text,
               has_attachments, attachments_json, reactions_json, topics_json,
               transcript, created_at
        FROM messages WHERE id = ? LIMIT 1
        """,
        (message_id,),
    )
    row = cur.fetchone()
    conn.close()

    if row is None:
        return None

    cols = [
        "id", "coleta_id", "grupo_id", "grupo_nome", "comunidade_nome", "coletado_em",
        "meses_back", "semanas_back", "data_hora", "data_hora_ts", "remetente",
        "texto", "texto_normalizado", "is_reply", "reply_author", "reply_text",
        "has_attachments", "attachments_json", "reactions_json", "topics_json",
        "transcript", "created_at",
    ]
    data = dict(zip(cols, row))
    data["has_attachments"] = bool(data.get("has_attachments"))
    return data


# =====================================================================
# 5. IMPORTAÇÃO E EXPORTAÇÃO (CSV / JSON)
# =====================================================================

def export_to_csv(
    path: str,
    limit: int | None = None,
    db_path: str | None = None,
) -> int:
    """Exporta mensagens para CSV (UTF-8 com separador ';')."""
    if db_path is None:
        db_path = get_db_path()
    init_db(db_path)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    q = (
        "SELECT id, coleta_id, grupo_id, grupo_nome, comunidade_nome, coletado_em, "
        "meses_back, semanas_back, data_hora, remetente, texto, texto_normalizado, "
        "is_reply, reply_author, reply_text, has_attachments, attachments_json, "
        "reactions_json, topics_json, transcript, created_at FROM messages "
        "ORDER BY data_hora_ts ASC"
    )
    params: list[Any] = []
    if limit is not None:
        q += " LIMIT ?"
        params.append(limit)

    cur.execute(q, params)
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    conn.close()

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(cols)
        for r in rows:
            writer.writerow(r)

    return len(rows)


def export_to_json(
    path: str,
    limit: int | None = None,
    db_path: str | None = None,
) -> int:
    """Exporta mensagens para JSON."""
    if db_path is None:
        db_path = get_db_path()
    init_db(db_path)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    q = "SELECT * FROM messages ORDER BY data_hora_ts ASC"
    params: list[Any] = []
    if limit is not None:
        q += " LIMIT ?"
        params.append(limit)

    cur.execute(q, params)
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    conn.close()

    items = [dict(zip(cols, r)) for r in rows]
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

    return len(items)


def import_from_csv_data(
    csv_content: str,
    db_path: str | None = None,
) -> tuple[int, str]:
    """
    Importa mensagens a partir do conteúdo de um arquivo CSV (separador ';' ou ',').
    Reseta o messages.db para acolher o novo grupo importado.
    Retorna uma tupla (total_importado, nome_do_grupo).
    """
    if not csv_content.strip():
        return (0, "")

    # Tenta detectar o dialeto / separador
    sample = csv_content[:2048]
    delimiter = ";" if ";" in sample else ","

    reader = csv.DictReader(io.StringIO(csv_content), delimiter=delimiter)
    messages = []
    grupo_identificado = "Grupo Importado"

    for row in reader:
        mid = row.get("id") or row.get("ID") or f"import_{datetime.now().timestamp()}_{len(messages)}"
        texto = row.get("texto") or row.get("message") or row.get("mensagem") or ""
        remetente = row.get("remetente") or row.get("author") or row.get("sender") or "Desconhecido"
        dh = row.get("data_hora") or row.get("timestamp") or datetime.now().isoformat()

        grupo_nome = (
            row.get("grupo_nome") or row.get("grupo") or row.get("group_name") or ""
        ).strip()
        if grupo_nome and grupo_identificado == "Grupo Importado":
            grupo_identificado = grupo_nome

        has_att = 1 if str(row.get("has_attachments", "")).lower() in ("1", "true", "sim") else 0

        messages.append(
            {
                "id": mid,
                "grupo_nome": grupo_nome or grupo_identificado,
                "remetente": remetente,
                "texto": texto,
                "data_hora": dh,
                "has_attachments": has_att,
            }
        )

    if not messages:
        return (0, "")

    # Reseta banco de mensagens para a nova sessão de grupo
    reset_messages_db(db_path=db_path)
    save_messages(messages, db_path=db_path)

    return (len(messages), grupo_identificado)


def import_from_json_data(
    json_content: str,
    db_path: str | None = None,
) -> tuple[int, str]:
    """
    Importa mensagens a partir do conteúdo de um arquivo JSON.
    Reseta o messages.db para acolher o novo grupo importado.
    Retorna uma tupla (total_importado, nome_do_grupo).
    """
    if not json_content.strip():
        return (0, "")

    dados = json.loads(json_content)
    if isinstance(dados, dict):
        dados = dados.get("messages") or dados.get("data") or [dados]

    if not isinstance(dados, list):
        return (0, "")

    messages = []
    grupo_identificado = "Grupo Importado"

    for item in dados:
        mid = item.get("id") or f"import_{datetime.now().timestamp()}_{len(messages)}"
        texto = item.get("texto") or item.get("message") or item.get("mensagem") or ""
        remetente = item.get("remetente") or item.get("author") or item.get("sender") or "Desconhecido"
        dh = item.get("data_hora") or item.get("timestamp") or datetime.now().isoformat()

        grupo_nome = (
            item.get("grupo_nome") or item.get("grupo") or item.get("group_name") or ""
        ).strip()
        if grupo_nome and grupo_identificado == "Grupo Importado":
            grupo_identificado = grupo_nome

        has_att = 1 if item.get("has_attachments") else 0

        messages.append(
            {
                "id": mid,
                "grupo_nome": grupo_nome or grupo_identificado,
                "remetente": remetente,
                "texto": texto,
                "data_hora": dh,
                "has_attachments": has_att,
            }
        )

    if not messages:
        return (0, "")

    # Reseta banco de mensagens para a nova sessão de grupo
    reset_messages_db(db_path=db_path)
    save_messages(messages, db_path=db_path)

    return (len(messages), grupo_identificado)
