from __future__ import annotations

import csv
import io
import json
import os
import re
import shutil
import sqlite3
import sys
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

# Permite leitura de CSV com campos grandes (ex: anexos base64)
try:
    csv.field_size_limit(sys.maxsize)
except OverflowError:
    csv.field_size_limit(2147483647)


from services.paths import (
    get_backups_dir,
    get_consultas_dir,
    get_data_dir,
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
            "active_db_filename": None,
            "active_group_id": None,
            "active_group_name": None,
            "last_updated": None,
            "total_messages": 0,
        }

    try:
        with open(state_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return {
                "active_db_filename": data.get("active_db_filename"),
                "active_group_id": data.get("active_group_id"),
                "active_group_name": data.get("active_group_name"),
                "last_updated": data.get("last_updated"),
                "total_messages": int(data.get("total_messages", 0)),
            }
    except Exception:
        return {
            "active_db_filename": None,
            "active_group_id": None,
            "active_group_name": None,
            "last_updated": None,
            "total_messages": 0,
        }


def set_active_group(
    group_id: str | None,
    group_name: str | None,
    total_messages: int = 0,
    db_filename: str | None = None,
) -> dict:
    """
    Salva na memória persistente qual o grupo e banco ativo atual na sessão.
    """
    state_path = get_state_file_path()
    current = get_app_state()
    active_filename = db_filename or current.get("active_db_filename")

    state = {
        "active_db_filename": active_filename,
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
    Limpa o grupo ativo do app_state.json, mantendo o arquivo de banco se houver.
    """
    current = get_app_state()
    return set_active_group(None, None, 0, db_filename=current.get("active_db_filename"))


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


def delete_catalog_group(identifier: str) -> bool:
    """
    Remove um grupo do arquivo data/groups_catalog.json buscando por id ou nome.
    Retorna True se o grupo foi encontrado e removido, False caso contrário.
    """
    catalog_path = get_groups_catalog_path()
    if not catalog_path.exists():
        return False

    grupos = load_catalog_groups()
    id_clean = str(identifier).strip().lower()
    novos_grupos = [
        g for g in grupos
        if str(g.get("id") or "").strip().lower() != id_clean
        and str(g.get("nome") or "").strip().lower() != id_clean
    ]

    if len(novos_grupos) == len(grupos):
        return False

    try:
        with open(catalog_path, "w", encoding="utf-8") as f:
            json.dump(novos_grupos, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"[ERRO] Falha ao atualizar groups_catalog.json após exclusão: {e}")
        return False


def delete_catalog_groups(identifiers: list[str]) -> int:
    """
    Remove múltiplos grupos do arquivo data/groups_catalog.json em lote.
    Retorna a quantidade de grupos removidos.
    """
    catalog_path = get_groups_catalog_path()
    if not catalog_path.exists() or not identifiers:
        return 0

    grupos = load_catalog_groups()
    ids_set = {str(i).strip().lower() for i in identifiers if i and str(i).strip()}
    if not ids_set:
        return 0

    novos_grupos = [
        g for g in grupos
        if str(g.get("id") or "").strip().lower() not in ids_set
        and str(g.get("nome") or "").strip().lower() not in ids_set
    ]

    removidos = len(grupos) - len(novos_grupos)
    if removidos > 0:
        try:
            with open(catalog_path, "w", encoding="utf-8") as f:
                json.dump(novos_grupos, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[ERRO] Falha ao atualizar groups_catalog.json após exclusão em lote: {e}")
            return 0

    return removidos


# =====================================================================
# 4. BANCO DE DADOS DE MENSAGENS (data/messages.db) - GRUPO ATIVO
# =====================================================================

def init_db(db_path: str | None = None) -> None:
    """
    Inicializa o banco SQLite messages.db.
    Garante as tabelas messages (com histórico cumulativo de grupos) e coletas_historico.
    """
    if db_path is None:
        db_path = get_db_path()

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # 1. Remove resquícios de tabelas legadas obsoletas
    cur.execute("DROP TABLE IF EXISTS known_groups")

    # 2. Criação da tabela messages dedicada às mensagens
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

    # 3. Criação da tabela de auditoria e histórico de coletas/consultas
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS coletas_historico (
            id TEXT PRIMARY KEY,
            grupo_id TEXT,
            grupo_nome TEXT,
            comunidade_nome TEXT,
            executado_em TEXT,
            unidade_tempo TEXT,
            valor INTEGER,
            tipo_filtro TEXT,
            total_extraido INTEGER,
            total_acumulado INTEGER,
            status TEXT,
            detalhes_json TEXT
        )
        """
    )

    # Migrações caso colunas novas faltem na tabela messages
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
    cur.execute("CREATE INDEX IF NOT EXISTS idx_coletas_executado_em ON coletas_historico(executado_em);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_coletas_grupo_id ON coletas_historico(grupo_id);")

    conn.commit()
    conn.close()


def detect_active_group_from_db(db_path: str | None = None) -> dict | None:
    """
    Inspeciona o messages.db.
    Se app_state.json tiver um grupo ativo com mensagens no banco, preserva-o.
    Caso contrário, detecta o grupo mais recentemente alimentado e atualiza o app_state.json.
    """
    if db_path is None:
        db_path = get_db_path()
    init_db(db_path)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    total_msgs_total = cur.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    if total_msgs_total == 0:
        conn.close()
        clear_active_group()
        return None

    # 1. Verifica se o grupo registrado no app_state ainda possui mensagens
    current_state = get_app_state()
    active_gid = current_state.get("active_group_id")
    active_gnome = current_state.get("active_group_name")

    if active_gid or active_gnome:
        conds = []
        params = []
        if active_gid:
            conds.append("grupo_id = ?")
            params.append(active_gid)
        if active_gnome:
            conds.append("grupo_nome = ?")
            params.append(active_gnome)
        
        q_check = f"SELECT COUNT(*) FROM messages WHERE {' OR '.join(conds)}"
        cur.execute(q_check, params)
        count_active = cur.fetchone()[0]
        if count_active > 0:
            conn.close()
            set_active_group(active_gid, active_gnome, count_active)
            return {
                "id": active_gid,
                "nome": active_gnome,
                "total_messages": count_active,
            }

    # 2. Se não houver grupo ativo fixado ou se não possuir mensagens, seleciona o grupo com dados mais recentes
    row = cur.execute(
        """
        SELECT grupo_id, grupo_nome, COUNT(*), MAX(data_hora_ts)
        FROM messages
        WHERE (grupo_nome IS NOT NULL AND TRIM(grupo_nome) != '')
           OR (grupo_id IS NOT NULL AND TRIM(grupo_id) != '')
        GROUP BY grupo_id, grupo_nome
        ORDER BY MAX(data_hora_ts) DESC, COUNT(*) DESC
        LIMIT 1
        """
    ).fetchone()

    conn.close()

    if row:
        gid = row[0]
        gnome = row[1] or "Grupo Importado"
        total_msgs = row[2]
        set_active_group(gid, gnome, total_msgs)
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
    Limpa completamente as mensagens e coletas do banco de dados local.
    Também limpa o estado ativo da aplicação.
    """
    if db_path is None:
        db_path = get_db_path()
    init_db(db_path)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("DELETE FROM messages")
    cur.execute("DELETE FROM coletas_historico")
    conn.commit()
    conn.close()

    clear_active_group()


def delete_group_messages(grupo_id_ou_nome: str, db_path: str | None = None) -> int:
    """
    Remove todas as mensagens e registros de coleta associados a um grupo específico
    do banco de dados SQLite (messages.db ou banco especificado).
    Se o grupo excluído for o grupo ativo atual, atualiza o app_state.
    Retorna o total de mensagens deletadas.
    """
    if db_path is None:
        db_path = get_db_path()
    init_db(db_path)

    ident = (grupo_id_ou_nome or "").strip()
    if not ident:
        return 0

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Conta quantas mensagens serão deletadas
    cur.execute(
        "SELECT COUNT(*) FROM messages WHERE grupo_id = ? OR grupo_nome = ? OR LOWER(grupo_nome) = LOWER(?)",
        (ident, ident, ident),
    )
    total_msgs = cur.fetchone()[0]

    # Deleta mensagens
    cur.execute(
        "DELETE FROM messages WHERE grupo_id = ? OR grupo_nome = ? OR LOWER(grupo_nome) = LOWER(?)",
        (ident, ident, ident),
    )

    # Deleta histórico de coletas correspondente
    cur.execute(
        "DELETE FROM coletas_historico WHERE grupo_id = ? OR grupo_nome = ? OR LOWER(grupo_nome) = LOWER(?)",
        (ident, ident, ident),
    )

    conn.commit()
    conn.close()

    # Se era o grupo ativo, ajusta o app_state
    app_state = get_app_state()
    active_gid = (app_state.get("active_group_id") or "").strip().lower()
    active_gnome = (app_state.get("active_group_name") or "").strip().lower()
    ident_lower = ident.lower()

    if ident_lower in (active_gid, active_gnome):
        detected = detect_active_group_from_db(db_path)
        if detected:
            set_active_group(
                group_id=detected.get("id"),
                group_name=detected.get("nome"),
                total_messages=detected.get("total_messages", 0),
            )
        else:
            clear_active_group()

    return total_msgs


def record_coleta_historico(
    coleta_data: dict,
    db_path: str | None = None,
) -> str:
    """
    Registra uma rodada de extração ou consulta na tabela coletas_historico.
    Retorna o ID da coleta registrada.
    """
    if db_path is None:
        db_path = get_db_path()
    init_db(db_path)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cid = coleta_data.get("id") or str(uuid.uuid4())
    executado_em = coleta_data.get("executado_em") or datetime.now().isoformat()
    grupo_id = coleta_data.get("grupo_id")
    grupo_nome = coleta_data.get("grupo_nome") or ""
    comunidade_nome = coleta_data.get("comunidade_nome") or ""
    unidade_tempo = coleta_data.get("unidade_tempo") or "dias"
    valor = int(coleta_data.get("valor", 7))
    tipo_filtro = coleta_data.get("tipo_filtro") or ""
    total_extraido = int(coleta_data.get("total_extraido", 0))
    total_acumulado = int(coleta_data.get("total_acumulado", 0))
    status = coleta_data.get("status") or "Concluído"
    detalhes_json = (
        json.dumps(coleta_data.get("detalhes", {}), ensure_ascii=False)
        if isinstance(coleta_data.get("detalhes"), dict)
        else (coleta_data.get("detalhes_json") or "{}")
    )

    cur.execute(
        """
        INSERT OR REPLACE INTO coletas_historico (
            id, grupo_id, grupo_nome, comunidade_nome, executado_em,
            unidade_tempo, valor, tipo_filtro, total_extraido,
            total_acumulado, status, detalhes_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            cid,
            grupo_id,
            grupo_nome,
            comunidade_nome,
            executado_em,
            unidade_tempo,
            valor,
            tipo_filtro,
            total_extraido,
            total_acumulado,
            status,
            detalhes_json,
        ),
    )
    conn.commit()
    conn.close()
    return cid


def list_coletas_historico(
    limit: int = 100,
    db_path: str | None = None,
) -> list[dict]:
    """
    Retorna o histórico cronológico decrescente de coletas e consultas realizadas.
    """
    if db_path is None:
        db_path = get_db_path()
    init_db(db_path)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute(
        """
        SELECT id, grupo_id, grupo_nome, comunidade_nome, executado_em,
               unidade_tempo, valor, tipo_filtro, total_extraido,
               total_acumulado, status, detalhes_json
        FROM coletas_historico
        ORDER BY executado_em DESC
        LIMIT ?
        """,
        (limit,),
    )
    rows = cur.fetchall()
    conn.close()

    result = []
    for r in rows:
        item = dict(r)
        try:
            item["detalhes"] = json.loads(item.get("detalhes_json") or "{}")
        except Exception:
            item["detalhes"] = {}
        result.append(item)
    return result


def list_grupos_historico(db_path: str | None = None) -> list[dict]:
    """
    Retorna a lista agregada de todos os grupos persistidos no banco de dados,
    com contagem de mensagens, primeira data, última data e última coleta.
    """
    if db_path is None:
        db_path = get_db_path()
    init_db(db_path)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute(
        """
        SELECT 
            COALESCE(NULLIF(TRIM(grupo_id), ''), 'grupo_desconhecido') AS gid,
            COALESCE(NULLIF(TRIM(grupo_nome), ''), 'Grupo Sem Nome') AS gnome,
            COUNT(*) AS total,
            MIN(data_hora) AS primeira_data,
            MAX(data_hora) AS ultima_data,
            MAX(coletado_em) AS ultima_coleta,
            MAX(data_hora_ts) AS max_ts
        FROM messages
        GROUP BY gid, gnome
        ORDER BY max_ts DESC, total DESC
        """
    )
    rows = cur.fetchall()
    conn.close()

    return [
        {
            "grupo_id": r[0],
            "grupo_nome": r[1],
            "total_mensagens": r[2],
            "primeira_data": r[3] or "-",
            "ultima_data": r[4] or "-",
            "ultima_coleta": r[5] or "-",
        }
        for r in rows
    ]


# =====================================================================
# GESTÃO DE CONSULTAS E BANCOS INDEPENDENTES (DATA/CONSULTAS/)
# =====================================================================

def create_new_consulta_db(group_name: str | None = None) -> tuple[str, str]:
    """
    Cria um novo banco de dados SQLite 100% independente para uma nova consulta em data/consultas/.
    Retorna (filename, absolute_path).
    """
    consultas_dir = get_consultas_dir()
    slug = gerar_grupo_id(group_name) if group_name else "consulta"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"consulta_{slug}_{timestamp}.db"
    db_path = str(consultas_dir / filename)

    # Inicializa o novo banco de dados isolado com o schema limpo
    init_db(db_path)

    # Define esse novo banco como o ativo no app_state.json
    state_path = get_state_file_path()
    current_state = get_app_state()
    current_state["active_db_filename"] = filename
    current_state["active_group_name"] = group_name
    current_state["active_group_id"] = gerar_grupo_id(group_name) if group_name else None
    current_state["total_messages"] = 0
    current_state["last_updated"] = datetime.now().isoformat()

    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(current_state, f, ensure_ascii=False, indent=2)

    return filename, db_path


def list_consultas() -> list[dict]:
    """
    Lista todos os bancos de dados independentes de consultas em data/consultas/ (e messages.db se existir).
    Retorna metadados como nome do arquivo, grupo principal, total de mensagens, tamanho, data de criação e se está ativo.
    """
    consultas_dir = get_consultas_dir()
    active_path_str = get_db_path()
    active_filename = Path(active_path_str).name

    arquivos_db = list(consultas_dir.glob("*.db"))

    # Inclui o messages.db raiz se existir e possuir conteúdo
    legacy_db = get_data_dir() / "messages.db"
    if legacy_db.exists() and legacy_db.stat().st_size > 0 and legacy_db not in arquivos_db:
        arquivos_db.append(legacy_db)

    # Ordena por data de modificação decrescente (mais recentes primeiro)
    arquivos_db.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    resultado = []
    for db_file in arquivos_db:
        try:
            stat = db_file.stat()
            size_kb = round(stat.st_size / 1024, 1)
            size_fmt = f"{size_kb} KB" if size_kb < 1024 else f"{round(size_kb/1024, 2)} MB"
            dt_modificacao = datetime.fromtimestamp(stat.st_mtime).strftime("%d/%m/%Y %H:%M:%S")

            # Inspeciona informações básicas dentro do banco SQLite
            conn = sqlite3.connect(str(db_file))
            cur = conn.cursor()
            try:
                cur.execute("SELECT count(*) FROM messages")
                total_msgs = cur.fetchone()[0]
            except Exception:
                total_msgs = 0

            try:
                cur.execute(
                    "SELECT DISTINCT grupo_nome FROM messages WHERE grupo_nome IS NOT NULL AND grupo_nome != '' LIMIT 5"
                )
                grupos = [r[0] for r in cur.fetchall()]
                grupo_principal = grupos[0] if grupos else "Sem grupo identificado"
            except Exception:
                grupo_principal = "Sem grupo identificado"
                grupos = []

            try:
                cur.execute("SELECT min(data_hora), max(data_hora) FROM messages WHERE data_hora IS NOT NULL")
                min_dt, max_dt = cur.fetchone()
            except Exception:
                min_dt, max_dt = None, None

            conn.close()

            is_active = (db_file.name == active_filename) or (str(db_file) == active_path_str)

            resultado.append({
                "filename": db_file.name,
                "path": str(db_file),
                "is_active": is_active,
                "grupo_principal": grupo_principal,
                "grupos": grupos,
                "total_mensagens": total_msgs,
                "tamanho_formatado": size_fmt,
                "tamanho_bytes": stat.st_size,
                "modificado_em": dt_modificacao,
                "periodo_inicio": min_dt or "-",
                "periodo_fim": max_dt or "-",
            })
        except Exception as e:
            print(f"[AVISO] Erro ao listar consulta {db_file.name}: {e}")

    return resultado


def get_active_consulta_info() -> dict:
    """
    Retorna as informações completas da consulta ativa no momento.
    """
    active_path_str = get_db_path()
    active_path = Path(active_path_str)
    filename = active_path.name

    total_msgs = 0
    grupo_principal = "Nenhum grupo ativo"
    if active_path.exists():
        try:
            conn = sqlite3.connect(active_path_str)
            cur = conn.cursor()
            total_msgs = cur.execute("SELECT count(*) FROM messages").fetchone()[0]
            grupo_row = cur.execute(
                "SELECT grupo_nome FROM messages WHERE grupo_nome IS NOT NULL AND grupo_nome != '' LIMIT 1"
            ).fetchone()
            if grupo_row and grupo_row[0]:
                grupo_principal = grupo_row[0]
            conn.close()
        except Exception:
            pass

    return {
        "filename": filename,
        "path": active_path_str,
        "grupo_principal": grupo_principal,
        "total_mensagens": total_msgs,
        "exists": active_path.exists(),
    }


def set_active_consulta(filename: str) -> dict:
    """
    Define a consulta informada como o banco ativo para Dashboard, Estatísticas e Chat do Gemini.
    """
    consultas_dir = get_consultas_dir()
    target_path = consultas_dir / filename
    if not target_path.exists():
        legacy_path = get_data_dir() / filename
        if legacy_path.exists():
            target_path = legacy_path
        else:
            raise FileNotFoundError(f"Banco de consulta '{filename}' não foi encontrado.")

    init_db(str(target_path))

    # Lê dados do banco
    conn = sqlite3.connect(str(target_path))
    cur = conn.cursor()
    total_msgs = cur.execute("SELECT count(*) FROM messages").fetchone()[0]
    grupo_row = cur.execute(
        "SELECT grupo_id, grupo_nome FROM messages WHERE grupo_nome IS NOT NULL AND grupo_nome != '' LIMIT 1"
    ).fetchone()
    conn.close()

    gid = grupo_row[0] if grupo_row else None
    gnome = grupo_row[1] if grupo_row else None

    state_path = get_state_file_path()
    current_state = get_app_state()
    current_state["active_db_filename"] = filename
    current_state["active_group_id"] = gid
    current_state["active_group_name"] = gnome
    current_state["total_messages"] = total_msgs
    current_state["last_updated"] = datetime.now().isoformat()

    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(current_state, f, ensure_ascii=False, indent=2)

    return {
        "success": True,
        "active_db_filename": filename,
        "active_group_name": gnome,
        "total_messages": total_msgs,
    }


def delete_consulta(filename: str) -> bool:
    """
    Exclui com segurança o arquivo .db de uma consulta independente.
    """
    consultas_dir = get_consultas_dir()
    target_path = consultas_dir / filename
    if not target_path.exists():
        legacy_path = get_data_dir() / filename
        if legacy_path.exists():
            target_path = legacy_path
        else:
            return False

    try:
        target_path.unlink()

        # Se era o banco ativo, busca o próximo banco disponível para ativar
        state = get_app_state()
        if state.get("active_db_filename") == filename:
            consultas_restantes = list(consultas_dir.glob("*.db"))
            if consultas_restantes:
                set_active_consulta(consultas_restantes[0].name)
            else:
                state_path = get_state_file_path()
                state["active_db_filename"] = None
                state["active_group_id"] = None
                state["active_group_name"] = None
                state["total_messages"] = 0
                with open(state_path, "w", encoding="utf-8") as f:
                    json.dump(state, f, ensure_ascii=False, indent=2)

        return True
    except Exception as e:
        print(f"[ERRO] Falha ao excluir consulta {filename}: {e}")
        return False


# =====================================================================
# GERENCIAMENTO AVANÇADO DE BACKUPS (DATA/BACKUPS)
# =====================================================================

def create_backup(
    tag: str = "manual",
    description: str = "",
    db_path: str | None = None,
) -> dict:
    """
    Cria uma cópia íntegra e atômica de backup do banco SQLite messages.db
    no diretório data/backups/, utilizando a SQLite Online Backup API.
    Gera também um arquivo de manifesto metadata companion (.json).
    """
    if db_path is None:
        db_path = get_db_path()
    init_db(db_path)

    backups_dir = get_backups_dir()
    now = datetime.now()
    stamp = now.strftime("%Y%m%d_%H%M%S")
    clean_tag = re.sub(r"[^\w-]", "_", tag).lower()
    backup_filename = f"backup_{stamp}_{clean_tag}.db"
    dest_path = backups_dir / backup_filename
    meta_path = backups_dir / f"backup_{stamp}_{clean_tag}.json"

    # Realiza o backup atômico via sqlite3 API
    src_conn = sqlite3.connect(db_path)
    dst_conn = sqlite3.connect(str(dest_path))
    try:
        src_conn.backup(dst_conn)
    finally:
        dst_conn.close()

    # Coleta estatísticas do snapshot gerado
    cur = src_conn.cursor()
    total_messages = cur.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    grupos_rows = cur.execute(
        "SELECT DISTINCT grupo_nome FROM messages WHERE grupo_nome IS NOT NULL AND grupo_nome != ''"
    ).fetchall()
    grupos = [g[0] for g in grupos_rows]
    src_conn.close()

    size_bytes = dest_path.stat().st_size if dest_path.exists() else 0

    metadata = {
        "filename": backup_filename,
        "path": str(dest_path),
        "created_at": now.isoformat(),
        "created_at_formatted": now.strftime("%d/%m/%Y %H:%M:%S"),
        "tag": clean_tag,
        "description": description or f"Backup snapshot ({clean_tag})",
        "size_bytes": size_bytes,
        "size_formatted": f"{size_bytes / 1024:.1f} KB" if size_bytes < 1048576 else f"{size_bytes / 1048576:.2f} MB",
        "total_messages": total_messages,
        "total_grupos": len(grupos),
        "grupos": grupos,
    }

    try:
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[AVISO] Não foi possível salvar manifesto de backup {meta_path}: {e}")

    return metadata


def list_backups() -> list[dict]:
    """
    Retorna a lista estruturada de todos os backups salvos em data/backups/,
    ordenados cronologicamente do mais recente para o mais antigo.
    """
    backups_dir = get_backups_dir()
    if not backups_dir.exists():
        return []

    backups = []
    db_files = list(backups_dir.glob("*.db"))

    for f in db_files:
        meta_file = backups_dir / f"{f.stem}.json"
        if meta_file.exists():
            try:
                with open(meta_file, "r", encoding="utf-8") as mf:
                    data = json.load(mf)
                    data["filename"] = f.name
                    data["path"] = str(f)
                    size = f.stat().st_size
                    data["size_bytes"] = size
                    data["size_formatted"] = f"{size / 1024:.1f} KB" if size < 1048576 else f"{size / 1048576:.2f} MB"
                    backups.append(data)
                    continue
            except Exception:
                pass

        # Fallback caso não haja arquivo .json de metadados
        size = f.stat().st_size
        mtime = datetime.fromtimestamp(f.stat().st_mtime)
        total_msgs = 0
        grupos = []
        try:
            b_conn = sqlite3.connect(str(f))
            b_cur = b_conn.cursor()
            total_msgs = b_cur.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            g_rows = b_cur.execute(
                "SELECT DISTINCT grupo_nome FROM messages WHERE grupo_nome IS NOT NULL AND grupo_nome != ''"
            ).fetchall()
            grupos = [g[0] for g in g_rows]
            b_conn.close()
        except Exception:
            pass

        tag = "manual"
        parts = f.stem.split("_")
        if len(parts) >= 4:
            tag = "_".join(parts[3:])

        backups.append(
            {
                "filename": f.name,
                "path": str(f),
                "created_at": mtime.isoformat(),
                "created_at_formatted": mtime.strftime("%d/%m/%Y %H:%M:%S"),
                "tag": tag,
                "description": f"Backup ({tag})",
                "size_bytes": size,
                "size_formatted": f"{size / 1024:.1f} KB" if size < 1048576 else f"{size / 1048576:.2f} MB",
                "total_messages": total_msgs,
                "total_grupos": len(grupos),
                "grupos": grupos,
            }
        )

    backups.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return backups


def restore_backup(filename: str, db_path: str | None = None) -> dict:
    """
    Restaura um backup a partir de um arquivo .db na pasta data/backups/.
    Antes da restauração, cria automaticamente um backup preventivo de segurança
    do estado atual do banco messages.db.
    """
    if db_path is None:
        db_path = get_db_path()

    backups_dir = get_backups_dir()
    clean_name = os.path.basename(filename)
    source_backup_file = backups_dir / clean_name

    if not source_backup_file.exists():
        raise FileNotFoundError(f"Arquivo de backup não encontrado: {clean_name}")

    # 1. Cria backup de segurança prévio do banco atual
    try:
        create_backup(tag="seguranca_pre_restauracao", description="Backup de segurança automático antes da restauração", db_path=db_path)
    except Exception as e:
        print(f"[AVISO] Falha ao gerar backup prévio de segurança: {e}")

    # 2. Restaura o arquivo selecionado para o messages.db via sqlite backup
    src_conn = sqlite3.connect(str(source_backup_file))
    dst_conn = sqlite3.connect(db_path)
    try:
        src_conn.backup(dst_conn)
    finally:
        src_conn.close()
        dst_conn.close()

    # 3. Detecta novo grupo ativo e sincroniza app_state
    detected = detect_active_group_from_db(db_path)
    total = count_messages(db_path)

    return {
        "success": True,
        "filename": clean_name,
        "total_messages": total,
        "active_group": detected,
        "message": f"Backup '{clean_name}' restaurado com sucesso! Total de mensagens ativas: {total}.",
    }


def delete_backup(filename: str) -> bool:
    """
    Remove um arquivo de backup específico e seu manifesto JSON.
    """
    backups_dir = get_backups_dir()
    clean_name = os.path.basename(filename)
    target_file = backups_dir / clean_name
    meta_file = backups_dir / f"{target_file.stem}.json"

    removed = False
    if target_file.exists():
        target_file.unlink(missing_ok=True)
        removed = True
    if meta_file.exists():
        meta_file.unlink(missing_ok=True)

    return removed


def get_persistence_stats(db_path: str | None = None) -> dict:
    """
    Retorna métricas consolidadas de persistência e histórico geral:
    - total_messages_all
    - total_groups_count
    - total_coletas_count
    - total_backups_count
    - db_size_bytes
    - backups_size_bytes
    - last_backup
    """
    if db_path is None:
        db_path = get_db_path()
    init_db(db_path)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    total_msgs = cur.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    total_grupos = cur.execute(
        "SELECT COUNT(DISTINCT COALESCE(NULLIF(TRIM(grupo_id), ''), grupo_nome)) FROM messages"
    ).fetchone()[0]
    total_coletas = cur.execute("SELECT COUNT(*) FROM coletas_historico").fetchone()[0]
    conn.close()

    db_p = Path(db_path)
    db_size = db_p.stat().st_size if db_p.exists() else 0

    backups = list_backups()
    backups_size = sum(b.get("size_bytes", 0) for b in backups)
    last_backup = backups[0] if backups else None

    return {
        "total_messages_all": total_msgs,
        "total_groups_count": total_grupos,
        "total_coletas_count": total_coletas,
        "total_backups_count": len(backups),
        "db_size_bytes": db_size,
        "db_size_formatted": f"{db_size / 1024:.1f} KB" if db_size < 1048576 else f"{db_size / 1048576:.2f} MB",
        "backups_size_bytes": backups_size,
        "backups_size_formatted": f"{backups_size / 1024:.1f} KB" if backups_size < 1048576 else f"{backups_size / 1048576:.2f} MB",
        "last_backup": last_backup,
    }


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

        # Suporte defensivo a campos de citação (direto ou aninhado em reply_data)
        is_reply_val = 1 if m.get("is_reply") else 0
        reply_author_val = m.get("reply_author")
        reply_text_val = m.get("reply_text")
        if not reply_author_val and m.get("reply_data"):
            reply_author_val = m["reply_data"].get("autor_citado")
        if not reply_text_val and m.get("reply_data"):
            reply_text_val = m["reply_data"].get("texto_citado")

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
                is_reply = coalesce(excluded.is_reply, messages.is_reply),
                reply_author = coalesce(excluded.reply_author, messages.reply_author),
                reply_text = coalesce(excluded.reply_text, messages.reply_text),
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
                is_reply_val,
                reply_author_val,
                reply_text_val,
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

    query = (
        "SELECT id, data_hora, remetente, texto, has_attachments, grupo_id, grupo_nome, "
        "is_reply, reply_author, reply_text FROM messages"
    )
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
            "is_reply": bool(r[7]),
            "reply_author": r[8],
            "reply_text": r[9],
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
    grupo_id: str | None = None,
    grupo_nome: str | None = None,
) -> int:
    """Exporta mensagens para CSV (UTF-8 com BOM e separador ';')."""
    if db_path is None:
        db_path = get_db_path()
    init_db(db_path)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    q = (
        "SELECT id, coleta_id, grupo_id, grupo_nome, comunidade_nome, coletado_em, "
        "meses_back, semanas_back, data_hora, remetente, texto, texto_normalizado, "
        "is_reply, reply_author, reply_text, has_attachments, attachments_json, "
        "reactions_json, topics_json, transcript, created_at FROM messages"
    )
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

    q += " ORDER BY data_hora_ts ASC"
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
    grupo_id: str | None = None,
    grupo_nome: str | None = None,
) -> int:
    """Exporta mensagens para JSON."""
    if db_path is None:
        db_path = get_db_path()
    init_db(db_path)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    q = "SELECT * FROM messages"
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

    q += " ORDER BY data_hora_ts ASC"
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
        is_rep = 1 if str(row.get("is_reply", "")).lower() in ("1", "true", "sim") else 0
        r_author = (row.get("reply_author") or "").strip() or None
        r_text = (row.get("reply_text") or "").strip() or None

        messages.append(
            {
                "id": mid,
                "grupo_nome": grupo_nome or grupo_identificado,
                "remetente": remetente,
                "texto": texto,
                "data_hora": dh,
                "has_attachments": has_att,
                "is_reply": is_rep,
                "reply_author": r_author,
                "reply_text": r_text,
            }
        )

    if not messages:
        return (0, "")

    # Salva mensagens cumulativamente sem apagar outros grupos
    novas = save_messages(messages, db_path=db_path)
    total_acumulado = count_messages(db_path=db_path, grupo_nome=grupo_identificado)

    # Registra no histórico de consultas/coletas
    record_coleta_historico(
        {
            "grupo_id": gerar_grupo_id(grupo_identificado),
            "grupo_nome": grupo_identificado,
            "comunidade_nome": "",
            "unidade_tempo": "arquivo",
            "valor": len(messages),
            "tipo_filtro": "importacao_csv",
            "total_extraido": len(messages),
            "total_acumulado": total_acumulado,
            "status": "Concluído (Importação CSV)",
            "detalhes": {"arquivo_tipo": "CSV", "novas_mensagens": novas},
        },
        db_path=db_path,
    )

    # Cria snapshot de backup automático pós-importação
    try:
        create_backup(
            tag="importacao_csv",
            description=f"Backup gerado após importação CSV do grupo '{grupo_identificado}' ({len(messages)} msgs)",
            db_path=db_path,
        )
    except Exception as e:
        print(f"[AVISO] Falha ao criar backup pós-importação CSV: {e}")

    return (len(messages), grupo_identificado)


def import_from_json_data(
    json_content: str,
    db_path: str | None = None,
) -> tuple[int, str]:
    """
    Importa mensagens a partir do conteúdo de um arquivo JSON.
    Preserva dados cumulativamente sem sobrescrever mensagens anteriores.
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
        is_rep = 1 if item.get("is_reply") else 0
        r_author = item.get("reply_author") or (item.get("reply_data") or {}).get("autor_citado")
        r_text = item.get("reply_text") or (item.get("reply_data") or {}).get("texto_citado")

        messages.append(
            {
                "id": mid,
                "grupo_nome": grupo_nome or grupo_identificado,
                "remetente": remetente,
                "texto": texto,
                "data_hora": dh,
                "has_attachments": has_att,
                "is_reply": is_rep,
                "reply_author": r_author,
                "reply_text": r_text,
            }
        )

    if not messages:
        return (0, "")

    # Salva mensagens cumulativamente sem apagar outros grupos
    novas = save_messages(messages, db_path=db_path)
    total_acumulado = count_messages(db_path=db_path, grupo_nome=grupo_identificado)

    # Registra no histórico de consultas/coletas
    record_coleta_historico(
        {
            "grupo_id": gerar_grupo_id(grupo_identificado),
            "grupo_nome": grupo_identificado,
            "comunidade_nome": "",
            "unidade_tempo": "arquivo",
            "valor": len(messages),
            "tipo_filtro": "importacao_json",
            "total_extraido": len(messages),
            "total_acumulado": total_acumulado,
            "status": "Concluído (Importação JSON)",
            "detalhes": {"arquivo_tipo": "JSON", "novas_mensagens": novas},
        },
        db_path=db_path,
    )

    # Cria snapshot de backup automático pós-importação
    try:
        create_backup(
            tag="importacao_json",
            description=f"Backup gerado após importação JSON do grupo '{grupo_identificado}' ({len(messages)} msgs)",
            db_path=db_path,
        )
    except Exception as e:
        print(f"[AVISO] Falha ao criar backup pós-importação JSON: {e}")

    return (len(messages), grupo_identificado)


def import_from_txt_data(
    txt_content: str,
    nome_grupo: str = "Conversa WhatsApp Importada",
    db_path: str | None = None,
) -> tuple[int, str]:
    """
    Importa mensagens a partir do conteúdo de um arquivo .txt ou .zip exportado do WhatsApp.
    Preserva dados cumulativamente sem sobrescrever mensagens anteriores.
    Retorna uma tupla (total_importado, nome_do_grupo).
    """
    if not txt_content.strip():
        return (0, "")

    from services.parser_txt import parse_whatsapp_txt

    messages = parse_whatsapp_txt(txt_content, nome_grupo_default=nome_grupo)
    if not messages:
        return (0, "")

    grupo_identificado = nome_grupo or "Conversa WhatsApp Importada"
    for m in messages:
        m["grupo_nome"] = grupo_identificado
        m["grupo_id"] = gerar_grupo_id(grupo_identificado)

    novas = save_messages(messages, db_path=db_path)
    total_acumulado = count_messages(db_path=db_path, grupo_nome=grupo_identificado)

    record_coleta_historico(
        {
            "grupo_id": gerar_grupo_id(grupo_identificado),
            "grupo_nome": grupo_identificado,
            "comunidade_nome": "",
            "unidade_tempo": "arquivo",
            "valor": len(messages),
            "tipo_filtro": "importacao_txt",
            "total_extraido": len(messages),
            "total_acumulado": total_acumulado,
            "status": "Concluído (Importação TXT WhatsApp)",
            "detalhes": {"arquivo_tipo": "TXT_WHATSAPP", "novas_mensagens": novas},
        },
        db_path=db_path,
    )

    try:
        create_backup(
            tag="importacao_txt",
            description=f"Backup gerado após importação TXT do grupo '{grupo_identificado}' ({len(messages)} msgs)",
            db_path=db_path,
        )
    except Exception as e:
        print(f"[AVISO] Falha ao criar backup pós-importação TXT: {e}")

    return (len(messages), grupo_identificado)


def load_catalog_groups_with_stats(db_path: str | None = None) -> list[dict]:
    """
    Retorna a lista de grupos catalogados enriquecida com a contagem de mensagens
    e datas persistidas no banco de dados SQLite/Neon.
    Também inclui grupos presentes no banco de dados que ainda não constem no catálogo.
    """
    if db_path is None:
        db_path = get_db_path()
    init_db(db_path)

    catalog = load_catalog_groups()
    stats_db = list_grupos_historico(db_path)
    mapa_stats = {
        (s.get("grupo_nome") or "").strip().lower(): s for s in stats_db if s.get("grupo_nome")
    }
    for s in stats_db:
        gid = (s.get("grupo_id") or "").strip().lower()
        if gid:
            mapa_stats[gid] = s

    resultado = []
    nomes_processados = set()

    for item in catalog:
        nome = (item.get("nome") or "").strip()
        gid = (item.get("id") or gerar_grupo_id(nome)).strip()
        chave_nome = nome.lower()
        chave_id = gid.lower()

        stat = mapa_stats.get(chave_nome) or mapa_stats.get(chave_id)
        total_msgs = stat.get("total_mensagens", 0) if stat else 0
        ult_data = stat.get("ultima_data", "-") if stat else "-"
        prim_data = stat.get("primeira_data", "-") if stat else "-"

        resultado.append({
            "id": gid,
            "nome": nome,
            "comunidade": item.get("comunidade") or "",
            "total_mensagens": total_msgs,
            "primeira_data": prim_data,
            "ultima_data": ult_data,
            "atualizado_em": item.get("atualizado_em") or "",
        })
        nomes_processados.add(chave_nome)
        nomes_processados.add(chave_id)

    # Adiciona grupos do banco de dados que ainda não estavam no catálogo JSON
    for s in stats_db:
        gnome = (s.get("grupo_nome") or "").strip()
        gid = (s.get("grupo_id") or gerar_grupo_id(gnome)).strip()
        if gnome.lower() not in nomes_processados and gid.lower() not in nomes_processados:
            resultado.append({
                "id": gid,
                "nome": gnome,
                "comunidade": "",
                "total_mensagens": s.get("total_mensagens", 0),
                "primeira_data": s.get("primeira_data", "-"),
                "ultima_data": s.get("ultima_data", "-"),
                "atualizado_em": s.get("ultima_coleta", "-"),
            })
            nomes_processados.add(gnome.lower())
            nomes_processados.add(gid.lower())

    return sorted(resultado, key=lambda g: (g["total_mensagens"] > 0, g["nome"].lower()), reverse=True)


def get_message_date_bounds(
    db_path: str | None = None,
    grupo_id: str | None = None,
    grupo_nome: str | None = None,
) -> dict:
    """
    Retorna os limites de datas (mínima e máxima) e timestamps das mensagens no banco local,
    filtrando opcionalmente pelo grupo especificado ou grupo ativo.
    Útil para configurar sliders de range temporal e estatísticas para a LLM.
    """
    if db_path is None:
        db_path = get_db_path()

    if not Path(db_path).exists():
        return {
            "has_data": False,
            "total_messages": 0,
            "min_ts": 0.0,
            "max_ts": 0.0,
            "min_date": "",
            "max_date": "",
            "grupo_nome": "",
        }

    # Se nenhum filtro for passado, tenta usar o grupo ativo do app_state
    if not grupo_id and not grupo_nome:
        app_state = get_app_state()
        grupo_id = app_state.get("active_group_id")
        grupo_nome = app_state.get("active_group_name")

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    where_clauses = ["data_hora_ts IS NOT NULL AND data_hora_ts > 0"]
    params: list[Any] = []

    if grupo_id or grupo_nome:
        filtros_grp = []
        if grupo_id:
            filtros_grp.append("grupo_id = ?")
            params.append(grupo_id)
        if grupo_nome:
            filtros_grp.append("grupo_nome = ?")
            params.append(grupo_nome)
        where_clauses.append(f"({' OR '.join(filtros_grp)})")

    where_sql = " WHERE " + " AND ".join(where_clauses)

    cur.execute(
        f"""
        SELECT 
            COUNT(*),
            MIN(data_hora_ts),
            MAX(data_hora_ts),
            MIN(data_hora),
            MAX(data_hora),
            COALESCE(MAX(grupo_nome), '')
        FROM messages
        {where_sql}
        """,
        params,
    )
    row = cur.fetchone()
    conn.close()

    total = row[0] if row and row[0] else 0
    if total == 0:
        return {
            "has_data": False,
            "total_messages": 0,
            "min_ts": 0.0,
            "max_ts": 0.0,
            "min_date": "",
            "max_date": "",
            "grupo_nome": grupo_nome or "",
        }

    min_ts = float(row[1]) if row[1] is not None else 0.0
    max_ts = float(row[2]) if row[2] is not None else 0.0
    min_date = str(row[3]) if row[3] else ""
    max_date = str(row[4]) if row[4] else ""
    gnome_res = str(row[5]) if row[5] else (grupo_nome or "")

    return {
        "has_data": True,
        "total_messages": total,
        "min_ts": min_ts,
        "max_ts": max_ts,
        "min_date": min_date,
        "max_date": max_date,
        "grupo_nome": gnome_res,
    }


def fetch_messages_for_llm_range(
    start_ts: float | None = None,
    end_ts: float | None = None,
    limit: int = 1500,
    db_path: str | None = None,
    grupo_id: str | None = None,
    grupo_nome: str | None = None,
) -> list[dict]:
    """
    Recupera mensagens dentro de um range temporal [start_ts, end_ts] ordenadas cronologicamente
    para alimentação do contexto da LLM, isolando pelo grupo ativo.
    """
    if db_path is None:
        db_path = get_db_path()

    if not Path(db_path).exists():
        return []

    # Se nenhum filtro for passado, tenta usar o grupo ativo do app_state
    if not grupo_id and not grupo_nome:
        app_state = get_app_state()
        grupo_id = app_state.get("active_group_id")
        grupo_nome = app_state.get("active_group_name")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    query = """
        SELECT id, data_hora, data_hora_ts, remetente, texto, is_reply, reply_author, reply_text, has_attachments, grupo_nome
        FROM messages
        WHERE 1=1
    """
    params: list[Any] = []

    if grupo_id or grupo_nome:
        filtros_grp = []
        if grupo_id:
            filtros_grp.append("grupo_id = ?")
            params.append(grupo_id)
        if grupo_nome:
            filtros_grp.append("grupo_nome = ?")
            params.append(grupo_nome)
        query += f" AND ({' OR '.join(filtros_grp)})"

    if start_ts is not None and start_ts > 0:
        query += " AND data_hora_ts >= ?"
        params.append(start_ts)

    if end_ts is not None and end_ts > 0:
        query += " AND data_hora_ts <= ?"
        params.append(end_ts)

    query += " ORDER BY data_hora_ts ASC LIMIT ?"
    params.append(limit)

    cur.execute(query, params)
    rows = cur.fetchall()
    conn.close()

    return [dict(r) for r in rows]


def wipe_all_data() -> tuple[bool, str]:
    """
    Limpa completamente todos os dados persistidos na pasta data/ (banco SQLite messages.db,
    catálogo de grupos, estado da aplicação, exports CSV e JSON).
    Em seguida, recria a pasta data/ e inicializa um novo banco de mensagens vazio.
    """
    data_dir = get_data_dir()
    if not data_dir.exists():
        data_dir.mkdir(parents=True, exist_ok=True)
        init_db(get_db_path())
        clear_active_group()
        return True, "Diretório de dados preparado com sucesso."

    try:
        # Remove todos os arquivos e subdiretórios da pasta data
        for item in data_dir.iterdir():
            try:
                if item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
                else:
                    item.unlink(missing_ok=True)
            except Exception:
                pass

        # Recria a estrutura e inicializa um banco limpo
        data_dir.mkdir(parents=True, exist_ok=True)
        init_db(get_db_path())
        clear_active_group()
        return True, "Todos os dados locais foram excluídos e a base foi reinicializada com sucesso."
    except Exception as e:
        return False, f"Erro ao limpar dados locais: {e}"

