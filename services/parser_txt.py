import hashlib
import io
import os
import re
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, List, Optional, Tuple, Union


# Expressões regulares para detecção de linhas de mensagens do WhatsApp
# Suporta:
# 1. [14:20, 12/08/2026] Autor: Mensagem (ou [12/08/2026, 14:20])
# 2. 12/08/2026 14:20 - Autor: Mensagem (ou 12/08/2026, 14:20 - Autor: Mensagem)
# 3. Formatos de 12h com AM/PM, segundos e múltiplos tipos de travessão (- / – / —)

PATTERN_BRACKETS = re.compile(
    r"^\["
    r"(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AaPp][Mm])?)"
    r"[,\s]+"
    r"(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AaPp][Mm])?)"
    r"\]\s*"
    r"(?:([^:]+?):\s*(.*)|(.*))$",
    re.DOTALL,
)

PATTERN_DASH = re.compile(
    r"^"
    r"(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4})"
    r"[,\s]+"
    r"(\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AaPp][Mm])?)"
    r"\s*[-–—]\s*"
    r"(?:([^:]+?):\s*(.*)|(.*))$",
    re.DOTALL,
)

# Termos que indicam notificações do sistema
SISTEMA_INDICATORS = [
    "as mensagens e as chamadas são protegidas",
    "mensagens e chamadas são protegidas",
    "criptografia de ponta a ponta",
    "messages and calls are end-to-end encrypted",
    "adicionou",
    "removeu",
    "saiu do grupo",
    "entrou usando o link",
    "mudou o nome do grupo",
    "mudou a imagem do grupo",
    "mudou a descrição do grupo",
    "você foi adicionado",
    "você saiu",
    "criou o grupo",
]

# Indicadores de mídia / anexo
MIDIA_INDICATORS = [
    "<arquivo de mídia oculto>",
    "<arquivo de midia oculto>",
    "<mídia oculta>",
    "<midia oculta>",
    "<anexado:",
    "<attached:",
    "arquivo anexado",
    "image omitted",
    "video omitted",
    "audio omitted",
    "sticker omitted",
    "document omitted",
    "contact card omitted",
    "poll omitted",
]


def _parse_datetime(parte1: str, parte2: str) -> Optional[datetime]:
    """
    Analisa duas strings (uma com data e outra com hora, em qualquer ordem)
    e retorna o objeto datetime correspondente com ano, mês, dia, hora e minuto corretos.
    """
    p1 = parte1.strip()
    p2 = parte2.strip()

    if "/" in p1 or "-" in p1 or "." in p1:
        data_str, hora_str = p1, p2
    else:
        hora_str, data_str = p1, p2

    data_clean = re.sub(r"[-.]", "/", data_str)
    partes_data = data_clean.split("/")
    if len(partes_data) != 3:
        return None

    try:
        p_a, p_b, p_c = [int(p) for p in partes_data]
    except ValueError:
        return None

    # Identifica dia, mês e ano
    if p_a > 1000:  # YYYY/MM/DD
        ano, mes, dia = p_a, p_b, p_c
    elif p_c > 1000:  # DD/MM/YYYY
        dia, mes, ano = p_a, p_b, p_c
    elif p_c < 100:  # DD/MM/YY
        dia, mes, ano = p_a, p_b, 2000 + p_c
    else:
        dia, mes, ano = p_a, p_b, p_c

    # Limpa caracteres invisíveis na hora
    hora_clean = re.sub(r"[\u202f\u00a0]", " ", hora_str).strip()

    # Tenta extrair hora, minuto e segundos
    m_hora = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?\s*([AaPp][Mm])?", hora_clean)
    if not m_hora:
        return None

    hh = int(m_hora.group(1))
    mm = int(m_hora.group(2))
    ss = int(m_hora.group(3)) if m_hora.group(3) else 0
    ampm = m_hora.group(4)

    if ampm:
        ampm_upper = ampm.upper()
        if ampm_upper == "PM" and hh < 12:
            hh += 12
        elif ampm_upper == "AM" and hh == 12:
            hh = 0

    try:
        return datetime(ano, mes, dia, hh, mm, ss)
    except ValueError:
        # Fallback para caso dia/mês estejam invertidos (MM/DD/YYYY)
        try:
            return datetime(ano, dia, mes, hh, mm, ss)
        except ValueError:
            return None


def _detectar_anexos_texto(texto: str) -> List[dict]:
    """
    Detecta menções a anexos ou mídias omitidas no texto da mensagem exportada.
    """
    texto_lower = texto.lower().strip()
    anexos = []

    if any(ind in texto_lower for ind in MIDIA_INDICATORS):
        tipo = "document"
        if "image" in texto_lower or "imagem" in texto_lower or "foto" in texto_lower:
            tipo = "image"
        elif "video" in texto_lower or "vídeo" in texto_lower:
            tipo = "video"
        elif "audio" in texto_lower or "áudio" in texto_lower or "voz" in texto_lower or "ptt" in texto_lower:
            tipo = "audio"
        elif "sticker" in texto_lower or "figurinha" in texto_lower:
            tipo = "sticker"
        elif "poll" in texto_lower or "enquete" in texto_lower:
            tipo = "poll"

        match_fn = re.search(r"<anexado:\s*(.+?)>", texto, re.IGNORECASE) or re.search(r"<attached:\s*(.+?)>", texto, re.IGNORECASE)
        filename = match_fn.group(1).strip() if match_fn else None

        anexos.append({"type": tipo, "filename": filename, "raw": texto.strip()})

    return anexos


def _gerar_id_mensagem(data_hora: datetime, remetente: str, texto: str, index: int) -> str:
    """
    Gera um ID único determinístico baseado no timestamp, remetente e início do texto.
    """
    raw_key = f"{data_hora.strftime('%Y%m%d_%H%M%S')}_{remetente}_{texto[:40]}_{index}"
    hash_digest = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:24]
    return f"wa_exp_{hash_digest}"


def parse_whatsapp_txt(
    conteudo_ou_caminho: Union[str, Path, bytes, BinaryIO],
    data_limite: Optional[datetime] = None,
    nome_grupo_default: str = "",
) -> List[dict]:
    """
    Analisa o conteúdo de uma exportação do WhatsApp (.txt ou .zip)
    e retorna uma lista estruturada de mensagens prontas para persistência no SQLite.

    :param conteudo_ou_caminho: Caminho do arquivo, string de texto, bytes ou objeto file-like.
    :param data_limite: Se informado, descarta mensagens anteriores a esta data.
    :param nome_grupo_default: Nome do grupo de fallback.
    :return: Lista de mensagens em formato de dicionário compatível com storage.save_messages().
    """
    linhas_texto: List[str] = []

    # 1. Obtenção do conteúdo de texto a partir de Path, Zip, Bytes ou String
    if isinstance(conteudo_ou_caminho, (str, Path)) and os.path.exists(str(conteudo_ou_caminho)):
        caminho = Path(conteudo_ou_caminho)
        if zipfile.is_zipfile(caminho):
            with zipfile.ZipFile(caminho, "r") as z:
                txt_names = [n for n in z.namelist() if n.lower().endswith(".txt")]
                if not txt_names:
                    raise ValueError(f"Nenhum arquivo .txt encontrado no arquivo compactado: {caminho.name}")
                with z.open(txt_names[0]) as f:
                    raw_bytes = f.read()
                    linhas_texto = raw_bytes.decode("utf-8-sig", errors="replace").splitlines()
        else:
            with open(caminho, "r", encoding="utf-8-sig", errors="replace") as f:
                linhas_texto = f.readlines()
    elif isinstance(conteudo_ou_caminho, bytes):
        if zipfile.is_zipfile(io.BytesIO(conteudo_ou_caminho)):
            with zipfile.ZipFile(io.BytesIO(conteudo_ou_caminho), "r") as z:
                txt_names = [n for n in z.namelist() if n.lower().endswith(".txt")]
                if not txt_names:
                    raise ValueError("Nenhum arquivo .txt encontrado no zip fornecido.")
                with z.open(txt_names[0]) as f:
                    linhas_texto = f.read().decode("utf-8-sig", errors="replace").splitlines()
        else:
            linhas_texto = conteudo_ou_caminho.decode("utf-8-sig", errors="replace").splitlines()
    elif hasattr(conteudo_ou_caminho, "read"):
        raw = conteudo_ou_caminho.read()
        if isinstance(raw, bytes):
            linhas_texto = raw.decode("utf-8-sig", errors="replace").splitlines()
        else:
            linhas_texto = str(raw).splitlines()
    elif isinstance(conteudo_ou_caminho, str):
        linhas_texto = conteudo_ou_caminho.splitlines()
    else:
        raise ValueError(f"Tipo de entrada não suportado: {type(conteudo_ou_caminho)}")

    # 2. Varredura e agrupamento de linhas (lidando com mensagens multilinhas)
    blocos_brutos: List[dict] = []
    mensagem_atual: Optional[dict] = None
    msg_index = 0

    for linha in linhas_texto:
        linha_limpa = linha.rstrip("\r\n")
        if not linha_limpa and not mensagem_atual:
            continue

        # Sanitiza marcas de direção e controle Unicode comuns no WhatsApp (\u200e, \u200f, \ufeff)
        linha_normalizada = re.sub(r"^[\u200e\u200f\ufeff\s]+", "", linha_limpa)
        # Substitui espaços especiais (narrow no-break space) por espaços normais
        linha_normalizada = re.sub(r"[\u202f\u00a0]", " ", linha_normalizada)

        match = PATTERN_BRACKETS.match(linha_normalizada) or PATTERN_DASH.match(linha_normalizada)
        if match:
            g1, g2, autor, texto, sistema_txt = match.groups()
            dt = _parse_datetime(g1, g2)

            if dt:
                if mensagem_atual:
                    blocos_brutos.append(mensagem_atual)
                    mensagem_atual = None

                if not autor:
                    continue

                autor = re.sub(r"[\u200e\u200f\ufeff]", "", autor).strip()
                texto_msg = re.sub(r"^[\u200e\u200f\ufeff]+", "", (texto or "")).strip()

                if any(ind in texto_msg.lower() for ind in SISTEMA_INDICATORS) and not autor:
                    continue

                msg_index += 1
                mensagem_atual = {
                    "index": msg_index,
                    "data_hora": dt,
                    "remetente": autor,
                    "linhas_texto": [texto_msg] if texto_msg else [],
                }
                continue

        if mensagem_atual is not None:
            mensagem_atual["linhas_texto"].append(linha_limpa)

    if mensagem_atual:
        blocos_brutos.append(mensagem_atual)

    # 3. Estruturação final das mensagens
    mensagens_finais: List[dict] = []
    for b in blocos_brutos:
        dt = b["data_hora"]
        if data_limite and dt < data_limite:
            continue

        texto_completo = "\n".join(b["linhas_texto"]).strip()
        remetente = b["remetente"]

        anexos = _detectar_anexos_texto(texto_completo)
        has_attachments = len(anexos) > 0

        if not texto_completo and not has_attachments:
            continue

        msg_id = _gerar_id_mensagem(dt, remetente, texto_completo, b["index"])

        mensagens_finais.append({
            "id": msg_id,
            "data_hora": dt,
            "data_hora_str": dt.strftime("%d/%m/%Y %H:%M"),
            "remetente": remetente,
            "texto": texto_completo,
            "is_reply": False,
            "reply_author": None,
            "reply_text": None,
            "reply_data": None,
            "attachments": anexos,
            "has_attachments": has_attachments,
        })

    mensagens_finais.sort(key=lambda x: x["data_hora"])
    return mensagens_finais
