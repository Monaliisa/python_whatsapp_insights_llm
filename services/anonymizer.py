"""
Módulo de Anonimização e Privacidade de Dados (LGPD) — WhatsApp Insights LLM
Filosofia: SOLID, Clean Architecture e Privacidade por Padrão (Privacy-by-Design)

Este serviço encapsula todas as regras de sanitização de mensagens de conversas do WhatsApp:
1. Mapeamento estável e persistente de remetentes para pseudônimos únicos (ex: 'membro-0001', 'aluno-0042').
2. Redação de telefones celulares/fixos brasileiros no corpo das mensagens -> [telefone].
3. Redação de e-mails -> [email].
4. Redação de URLs pessoais/profissionais (LinkedIn, GitHub, Instagram, Twitter/X, wa.me, etc.) -> [linkedin], [github], etc.
5. Redação de menções diretas a nomes de usuários (@Nome) geradas pelo WhatsApp -> @[mencao].
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

from services.paths import get_data_dir

logger = logging.getLogger(__name__)

# Expressões regulares para detecção de dados sensíveis
RE_TELEFONE = re.compile(
    r"(?<!\d)(?:\+?55\s?)?\(?\d{2}\)?\s?9?\s?\d{4}[-.\s]?\d{4}(?!\d)"
)
RE_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

# URLs de perfis pessoais e links de contato que podem identificar o remetente
URLS_PESSOAIS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"(?:https?://)?(?:www\.)?linkedin\.com/[\w%\-./?=&+~]*", re.I), "[linkedin]"),
    (re.compile(r"(?:https?://)?(?:www\.)?github\.(?:com|io)/[\w%\-./?=&+~]*", re.I), "[github]"),
    (re.compile(r"(?:https?://)?(?:www\.)?instagram\.com/[\w%\-./?=&+~]*", re.I), "[instagram]"),
    (re.compile(r"(?:https?://)?(?:www\.)?(?:x|twitter)\.com/[\w%\-./?=&+~]*", re.I), "[x]"),
    (re.compile(r"(?:https?://)?(?:www\.)?(?:facebook|fb)\.com/[\w%\-./?=&+~]*", re.I), "[facebook]"),
    (re.compile(r"(?:https?://)?(?:api\.)?wa\.me/[\w%\-./?=&+~]*", re.I), "[whatsapp]"),
    (re.compile(r"(?:https?://)?chat\.whatsapp\.com/[\w%\-./?=&+~]*", re.I), "[grupo-whatsapp]"),
    (re.compile(r"(?:https?://)?t\.me/[\w%\-./?=&+~]*", re.I), "[telegram]"),
    (re.compile(r"(?:https?://)?lnkd\.in/[\w%\-./?=&+~]*", re.I), "[linkedin]"),
    # Marcações @Nome geradas pelo WhatsApp (delimitadores unicode U+2068 e U+2069)
    (re.compile(r"@\u2068(?!todos\u2069)[^\u2069]*\u2069"), "@[mencao]"),
    # Menções simples no padrão @Nome (evitando @todos e menções comuns de canais)
    (re.compile(r"@(?!(?:todos|here|channel|alguem|alguém)\b)[a-zA-Z0-9_.-]{3,30}"), "@[mencao]"),
    # Subdomínios de apps pessoais em plataformas de deploy rápido
    (re.compile(r"(?:https?://)?[\w\-]+\.(?:streamlit\.app|lovable\.app|vercel\.app|netlify\.app|github\.io)/?[\w%\-./?=&+~]*", re.I), "[app-pessoal]"),
)


def normalizar_identidade(identidade: str) -> str:
    """Normaliza strings de remetente para comparação estável."""
    return unicodedata.normalize("NFC", identidade or "").strip()


class AnonymizerService:
    """
    Serviço central de anonimização e conformidade com LGPD para o ZapInsights.
    Mantém mapa persistente em disco para garantir que o mesmo participante
    receba sempre o mesmo pseudônimo determinístico.
    """

    def __init__(self, mapa_path: Path | str | None = None, prefixo_pseudonimo: str = "aluno"):
        if mapa_path is None:
            self.mapa_path = get_data_dir() / "pseudonimos.json"
        else:
            self.mapa_path = Path(mapa_path)
        self.prefixo = prefixo_pseudonimo
        self._mapa: dict[str, str] = {}
        self._carregar_mapa()

    def _carregar_mapa(self) -> None:
        """Carrega o mapa de pseudônimos existente do arquivo JSON."""
        if self.mapa_path.exists():
            try:
                content = self.mapa_path.read_text(encoding="utf-8")
                self._mapa = json.loads(content)
            except Exception as e:
                logger.warning(f"Não foi possível carregar mapa de pseudônimos ({e}). Criando novo.")
                self._mapa = {}
        else:
            self._mapa = {}

    def _salvar_mapa(self) -> None:
        """Persiste o mapa de pseudônimos no disco de forma atômica/segura."""
        try:
            self.mapa_path.parent.mkdir(parents=True, exist_ok=True)
            self.mapa_path.write_text(
                json.dumps(self._mapa, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error(f"Erro ao salvar mapa de pseudônimos em {self.mapa_path}: {e}")

    def pseudonimizar_remetente(self, remetente: str) -> str:
        """
        Retorna o pseudônimo estável para um determinado remetente (ex: 'aluno-0001').
        Se o remetente já for um pseudônimo ou termo genérico, retorna inalterado.
        """
        chave = normalizar_identidade(remetente)
        if not chave:
            return f"{self.prefixo}-0000"

        # Se já é um pseudônimo conhecido ou genérico
        if re.match(rf"^(?:aluno|membro|participante)-\d{{4}}$", chave, re.I):
            return chave.lower()
        if chave.lower() in ("sistema", "whatsapp", "admin", "administrador"):
            return chave

        if chave not in self._mapa:
            novo_num = len(self._mapa) + 1
            self._mapa[chave] = f"{self.prefixo}-{novo_num:04d}"
            self._salvar_mapa()

        return self._mapa[chave]

    def redigir_texto(self, texto: str, contador: Counter | None = None) -> str:
        """
        Sanitiza o corpo de uma mensagem, substituindo telefones, e-mails, menções
        e URLs de perfis por marcas analíticas descritivas.
        """
        if not texto:
            return ""

        texto_limpo = texto

        # 1. Redige e-mails primeiro (evita colisão com regex de menções @)
        texto_limpo, n_email = RE_EMAIL.subn("[email]", texto_limpo)
        if n_email and contador is not None:
            contador["[email]"] += n_email

        # 2. Redige telefones
        texto_limpo, n_tel = RE_TELEFONE.subn("[telefone]", texto_limpo)
        if n_tel and contador is not None:
            contador["[telefone]"] += n_tel

        # 3. Redige URLs pessoais e menções
        for rx, marca in URLS_PESSOAIS:
            texto_limpo, n = rx.subn(marca, texto_limpo)
            if n and contador is not None:
                contador[marca] += n

        return texto_limpo

    def anonimizar_mensagem(self, msg: dict[str, Any], contador: Counter | None = None) -> dict[str, Any]:
        """
        Retorna uma cópia da mensagem com remetente, reply_author e textos sanitizados.
        """
        msg_copia = dict(msg)
        remetente_orig = msg.get("remetente") or ""
        msg_copia["remetente"] = self.pseudonimizar_remetente(remetente_orig)

        reply_author_orig = msg.get("reply_author") or ""
        if reply_author_orig:
            msg_copia["reply_author"] = self.pseudonimizar_remetente(reply_author_orig)

        texto_orig = msg.get("texto") or ""
        msg_copia["texto"] = self.redigir_texto(texto_orig, contador)

        reply_text_orig = msg.get("reply_text") or ""
        if reply_text_orig:
            msg_copia["reply_text"] = self.redigir_texto(reply_text_orig, contador)

        return msg_copia

    def anonimizar_mensagens(
        self, mensagens: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        """
        Anonimiza uma lista inteira de mensagens e retorna a lista limpa
        juntamente com o sumário quantitativo de redações efetuadas.
        """
        contador: Counter = Counter()
        mensagens_limpas = [self.anonimizar_mensagem(m, contador) for m in mensagens]
        stats = dict(sorted(contador.items()))
        return mensagens_limpas, stats

    def total_pseudonimos(self) -> int:
        """Retorna o total de identidades mapeadas até o momento."""
        return len(self._mapa)


# Instância singleton para uso em toda a aplicação
_instancia_anonymizer: AnonymizerService | None = None


def get_anonymizer_service() -> AnonymizerService:
    """Retorna a instância singleton do serviço de anonimização."""
    global _instancia_anonymizer
    if _instancia_anonymizer is None:
        _instancia_anonymizer = AnonymizerService()
    return _instancia_anonymizer
