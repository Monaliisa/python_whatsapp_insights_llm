"""
Módulo de Integração com Google Gemini (LLM) — WhatsApp Insights LLM
Filosofia: Bring Your Own Key (BYOK)

Este serviço encapsula todas as interações com os Modelos de Linguagem do Google Gemini,
permitindo validação de credenciais fornecidas pelo usuário, seleção dinâmica de modelos
e orquestração de prompts analíticos sobre as mensagens extraídas.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Modelos recomendados e catalogados da família Google Gemini
MODELOS_DISPONIVEIS = [
    {
        "id": "gemini-2.5-flash",
        "nome": "Gemini 2.5 Flash",
        "descricao": "Recomendado. Rápido, eficiente e com ótima capacidade de síntese para grandes volumes de conversas.",
        "tipo": "balanceado",
        "recomendado": True,
    },
    {
        "id": "gemini-2.5-pro",
        "nome": "Gemini 2.5 Pro",
        "descricao": "Raciocínio avançado e análise qualitativa aprofundada. Ideal para análises temáticas complexas.",
        "tipo": "avancado",
        "recomendado": False,
    },
    {
        "id": "gemini-3.7-flash",
        "nome": "Gemini 3.7 Flash",
        "descricao": "Última geração com velocidade superior e raciocínio multimodal otimizado.",
        "tipo": "alta_performance",
        "recomendado": False,
    },
    {
        "id": "gemini-3.5-flash-lite",
        "nome": "Gemini 3.5 Flash Lite",
        "descricao": "Execução ultra leve e baixo consumo de cota de tokens.",
        "tipo": "leve",
        "recomendado": False,
    },
]


class GeminiService:
    """
    Serviço responsável pela conexão e inferência via Google Gemini API.
    Segue a filosofia BYOK (Bring Your Own Key), onde a chave é fornecida sob demanda.
    """

    def __init__(self, api_key: str | None = None, model: str = "gemini-2.5-flash"):
        self.api_key = api_key.strip() if api_key else None
        self.model = model

    @classmethod
    def listar_modelos(cls) -> list[dict[str, Any]]:
        """Retorna a lista de modelos recomendados e suas características."""
        return list(MODELOS_DISPONIVEIS)

    def validar_api_key(self, api_key: str | None = None, model: str | None = None) -> tuple[bool, str]:
        """
        Testa a validade da API Key informada fazendo uma chamada de teste mínima na API do Gemini.
        Retorna (sucesso: bool, mensagem_ou_erro: str).
        """
        chave = (api_key or self.api_key or "").strip()
        modelo_alvo = (model or self.model or "gemini-2.5-flash").strip()

        if not chave:
            return False, "Nenhuma API Key informada. Forneça sua chave do Google Gemini."

        try:
            from google import genai

            # Instancia o cliente oficial do Google GenAI com a chave BYOK
            client = genai.Client(api_key=chave)

            # Executa uma inferência curta de verificação de conectividade e autorização
            response = client.models.generate_content(
                model=modelo_alvo,
                contents="Responda apenas 'OK' em texto.",
            )

            texto_resposta = (response.text or "").strip()
            return True, f"Chave validada com sucesso via modelo '{modelo_alvo}'! Conexão ativa com o Google Gemini."

        except ImportError:
            # Fallback caso a biblioteca google-genai ainda não esteja instalada no ambiente
            import json
            import urllib.error
            import urllib.request

            # Teste HTTP direto no endpoint oficial do Gemini como fallback resiliente
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo_alvo}:generateContent?key={chave}"
            payload = json.dumps({
                "contents": [{"parts": [{"text": "Responda apenas 'OK'."}]}]
            }).encode("utf-8")

            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            try:
                with urllib.request.urlopen(req, timeout=12) as res:
                    if res.status == 200:
                        return True, f"Chave validada com sucesso via modelo '{modelo_alvo}'!"
                    return False, f"Resposta inesperada da API: HTTP {res.status}"
            except urllib.error.HTTPError as http_err:
                if http_err.code in (400, 403):
                    return False, "API Key inválida ou sem permissão de acesso aos modelos Gemini no Google AI Studio."
                if http_err.code == 404:
                    return False, f"Modelo '{modelo_alvo}' não encontrado ou indisponível para esta chave."
                return False, f"Erro HTTP {http_err.code} ao validar chave: {http_err.reason}"
            except Exception as e:
                return False, f"Falha de conexão com a API do Google Gemini: {e}"

        except Exception as exc:
            msg = str(exc)
            if "API_KEY_INVALID" in msg or "400" in msg or "403" in msg or "permission" in msg.lower():
                return False, "API Key inválida ou sem permissão no Google AI Studio. Verifique a chave digitada."
            if "RESOURCE_EXHAUSTED" in msg or "429" in msg:
                return False, "Limite de cota de requisições excedido para esta chave no momento (HTTP 429)."
            return False, f"Erro ao comunicar com Google Gemini: {msg}"
