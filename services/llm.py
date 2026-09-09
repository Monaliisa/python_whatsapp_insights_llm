"""
Módulo de Integração com Anthropic (Claude) — WhatsApp Insights LLM

Este serviço encapsula todas as interações com os Modelos de Linguagem da Anthropic (Claude),
permitindo validação de credenciais fornecidas pelo usuário ou via variável de ambiente,
seleção dinâmica de modelos, engenharia de prompts especializada e execução de chat
analítico sobre as mensagens extraídas.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Modelos catalogados da família Anthropic Claude
MODELOS_DISPONIVEIS = [
    {
        "id": "claude-3-5-sonnet-20241022",
        "nome": "Claude 3.5 Sonnet",
        "descricao": "Recomendado. Rápido, com capacidade analítica excepcional e síntese de alta precisão.",
        "tipo": "balanceado",
        "recomendado": True,
    },
    {
        "id": "claude-3-5-haiku-20241022",
        "nome": "Claude 3.5 Haiku",
        "descricao": "Velocidade ultra rápida e ótimo custo-benefício para respostas dinâmicas.",
        "tipo": "leve",
        "recomendado": False,
    },
    {
        "id": "claude-3-opus-20240229",
        "nome": "Claude 3 Opus",
        "descricao": "Raciocínio profundo e análise qualitativa exaustiva para tópicos complexos.",
        "tipo": "avancado",
        "recomendado": False,
    },
]

# System Prompt base com foco em Engenharia de Prompt e análise comunitária
SYSTEM_PROMPT_BASE = """Você é o ZapInsights AI, um assistente especialista em inteligência de dados, análise qualitativa e gestão de comunidades e grupos de estudo do WhatsApp.

Sua missão é processar mensagens reais extraídas de conversas e fornecer diagnósticos analíticos precisos, sínteses claras e planos de ação objetivos para gestores, educadores e moderadores.

DIRETRIZES E REGRAS DE OURO:
1. **Fidelidade estrita aos dados**: Baseie suas respostas única e exclusivamente no histórico de mensagens fornecido no contexto. Se alguma informação ou detalhe não constar nas mensagens analisadas, declare com clareza em vez de inventar fatos ou presumir eventos.
2. **Formatação visual rica em Markdown**:
   - Utilize cabeçalhos bem estruturados (`##`, `###`).
   - Use listas com marcadores (`-`), destaques em **negrito** e *itálico* para facilitar leitura dinâmica.
   - Sempre que couber, organize categorias, frequências ou comparações em tabelas Markdown elegantes.
   - Use blocos de citação (`>`) para ilustrar com falas ou menções textuais expressivas dos membros.
3. **Tom de voz**: Profissional, analítico, acolhedor, empático e com foco em aplicabilidade prática.
4. **Respeito à privacidade**: Evite expor números de telefone completos ou dados sensíveis que possam constar nos textos.
5. **Idioma**: Responda sempre em Português do Brasil de forma fluida e elegante.
"""

# Templates especializados para Análises Úteis
ANALISES_PRE_PROGRAMADAS = {
    "resumo_destaques": {
        "id": "resumo_destaques",
        "titulo": "Resumo Executivo e Destaques",
        "icone": "📊",
        "badge": "Síntese Geral",
        "descricao": "Síntese dos tópicos mais debatidos, pontos de virada e materiais compartilhados no período.",
        "frase_chat": "Gere um resumo executivo com os principais destaques do período.",
        "prompt_template": """Elabore um **Resumo Executivo e Destaques** das conversas do grupo selecionado no período.
Sua análise deve conter:
1. **📌 Panorama Geral do Período**: Volume de atividade, intensidade das conversas e momentos de pico.
2. **🔥 Tópicos e Debates Centrais**: Quais foram os assuntos que mais movimentaram a comunidade, organizados por relevância.
3. **💡 Conclusões e Consensos**: Quais dúvidas foram resolvidas ou conclusões foram alcançadas pelos membros.
4. **🔗 Recursos e Materiais Relevantes**: Links, ferramentas, livros, tutoriais ou materiais mais citados ou elogiados.
5. **🎯 Destaque Principal**: A mensagem ou debate de maior impacto do período.
""",
    },
    "duvidas_recorrentes": {
        "id": "duvidas_recorrentes",
        "titulo": "Dúvidas e Dificuldades Recorrentes",
        "icone": "💡",
        "badge": "Gargalos & Dores",
        "descricao": "Mapeamento das principais dúvidas conceituais, técnicas e pedagógicas trazidas pelos alunos.",
        "frase_chat": "Mapeie as dúvidas e dificuldades mais recorrentes levantadas pelos participantes.",
        "prompt_template": """Faça um **Mapeamento Aprofundado de Dúvidas e Dificuldades** a partir das mensagens do período.
Sua análise deve conter:
1. **📋 Eixos Temáticos de Dificuldade**: Agrupe as dúvidas por categoria (ex: Ferramentas, Conceitos Teóricos, Ambiente/Instalação, Exercícios práticos).
2. **📊 Tabela de Dúvidas Frequentes**: Uma tabela com colunas: *Tema*, *Descrição da Dúvida*, *Complexidade Estimada (Básico / Intermediário / Avançado)* e *Situação (Resolvida pelo grupo / Sem resposta conclusiva)*.
3. **⚠️ Gargalos Críticos**: Dificuldades que travaram múltiplos participantes ou geraram repetições.
4. **🤝 Resoluções Comunitárias**: Como os próprios colegas colaboraram para resolver os problemas.
""",
    },
    "analise_sentimento": {
        "id": "analise_sentimento",
        "titulo": "Análise de Sentimento e Clima",
        "icone": "📈",
        "badge": "Engajamento & Clima",
        "descricao": "Termômetro emocional, engajamento dos membros, potenciais atritos e momentos de celebração.",
        "frase_chat": "Faça uma análise de sentimento, engajamento e clima da comunidade no período.",
        "prompt_template": """Realize uma **Análise de Sentimento e Diagnóstico do Clima da Comunidade** no período analisado.
Sua análise deve conter:
1. **🌡️ Termômetro Geral da Comunidade**: Classifique o sentimento predominante (ex: Muito Positivo / Otimista / Ansioso / Neutro / Frustrado) com percentuais aproximados e justificativas.
2. **✨ Momentos de Celebração e Conquista**: Relatos de sucesso, vitórias em projetos, contratações, agradecimentos ou elogios.
3. **⚠️ Pontos de Atrito ou Insegurança**: Expressões de desânimo, sobrecarga de estudos, desentendimentos ou frustrações com ferramentas.
4. **👥 Dinâmica de Colaboração**: Nível de interajuda entre os participantes (comunidade acolhedora vs. isolada).
""",
    },
    "plano_de_acao": {
        "id": "plano_de_acao",
        "titulo": "Recomendações e Plano de Ação",
        "icone": "🎯",
        "badge": "Gestão & Ação",
        "descricao": "Sugestões práticas de conteúdos, lives, FAQs e intervenções para moderadores e líderes.",
        "frase_chat": "Proponha recomendações práticas e um plano de ação para a gestão da comunidade.",
        "prompt_template": """Com base exclusivamente nas necessidades e conversas reais identificadas no período, elabore um **Plano de Ação Estratégico** para os instrutores, líderes ou moderadores da comunidade.
Sua resposta deve conter:
1. **⚡ Intervenções Imediatas (Próximas 24h a 48h)**: Mensagens de esclarecimento, acolhimento de dúvidas em aberto ou orientações pontuais que a moderação deve fazer no grupo.
2. **📚 Propostas de Conteúdo e Reforço**: Temas urgentes para novas lives, tutoriais rápidos, posts de fixação ou FAQs explicativas.
3. **🌟 Estratégia de Engajamento Comunitário**: Como reativar participantes silenciosos e como reconhecer/recompensar os membros mais colaborativos.
4. **🗓️ Checklist de Execução**: Um checklist prático (com caixas de seleção `[ ]`) priorizado por impacto.
""",
    },
}


def formatar_contexto_mensagens(mensagens: list[dict], max_chars: int = 400000) -> str:
    """
    Formata a lista de mensagens extraídas do SQLite em um formato de log estruturado,
    enxuto e otimizado para o consumo de contexto pela LLM.
    """
    if not mensagens:
        return "Nenhuma mensagem encontrada no período selecionado."

    linhas = []
    chars_acumulados = 0

    for msg in mensagens:
        dh = msg.get("data_hora") or "Sem data"
        remetente = msg.get("remetente") or "Participante"
        texto = (msg.get("texto") or "").strip()
        is_reply = bool(msg.get("is_reply"))
        reply_author = msg.get("reply_author") or ""
        reply_text = (msg.get("reply_text") or "").strip()
        has_att = bool(msg.get("has_attachments"))

        prefixo_reply = f" [Em resposta a @{reply_author}: \"{reply_text[:60]}...\"]" if is_reply and reply_author else ""
        sufixo_att = " [Anexo]" if has_att and not texto else ""

        linha = f"[{dh}] @{remetente}{prefixo_reply}: {texto}{sufixo_att}"
        chars_acumulados += len(linha)

        if chars_acumulados > max_chars:
            linhas.append("... [Contexto truncado devido ao limite de tamanho] ...")
            break

        linhas.append(linha)

    return "\n".join(linhas)


class AnthropicService:
    """
    Serviço responsável pela conexão e inferência via Anthropic (Claude) API.
    Suporta chaves informadas sob demanda na interface ou via variável de ambiente ANTHROPIC_API_KEY.
    """

    def __init__(self, api_key: str | None = None, model: str = "claude-3-5-sonnet-20241022"):
        chave_informada = (api_key or "").strip()
        chave_env = os.getenv("ANTHROPIC_API_KEY", "").strip()
        self.api_key = chave_informada if chave_informada else (chave_env if chave_env else None)
        self.model = model or "claude-3-5-sonnet-20241022"

    @classmethod
    def listar_modelos(cls) -> list[dict[str, Any]]:
        """Retorna a lista de modelos recomendados e suas características."""
        return list(MODELOS_DISPONIVEIS)

    @classmethod
    def listar_analises_uteis(cls) -> list[dict[str, Any]]:
        """Retorna a lista estruturada de análises úteis para popular a interface."""
        return list(ANALISES_PRE_PROGRAMADAS.values())

    def validar_api_key(self, api_key: str | None = None, model: str | None = None) -> tuple[bool, str]:
        """
        Testa a validade da API Key informada fazendo uma chamada de teste mínima na API da Anthropic.
        Retorna (sucesso: bool, mensagem_ou_erro: str).
        """
        chave = (api_key or self.api_key or os.getenv("ANTHROPIC_API_KEY", "")).strip()
        modelo_alvo = (model or self.model or "claude-3-5-sonnet-20241022").strip()

        if not chave:
            return False, "Nenhuma API Key informada. Forneça sua chave da Anthropic (iniciada com 'sk-ant-')."

        try:
            import anthropic

            client = anthropic.Anthropic(api_key=chave)
            # Chamada curta de validação de autenticação e permissão
            response = client.messages.create(
                model=modelo_alvo,
                max_tokens=10,
                messages=[{"role": "user", "content": "Responda apenas 'OK'."}],
            )

            return True, f"Chave validada com sucesso via modelo '{modelo_alvo}'! Conexão ativa com a Anthropic."

        except ImportError:
            # Fallback HTTP REST nativo caso a biblioteca 'anthropic' ainda não esteja instalada
            import urllib.error
            import urllib.request

            url = "https://api.anthropic.com/v1/messages"
            payload = json.dumps({
                "model": modelo_alvo,
                "max_tokens": 10,
                "messages": [{"role": "user", "content": "Responda apenas 'OK'."}],
            }).encode("utf-8")

            req = urllib.request.Request(
                url,
                data=payload,
                headers={
                    "x-api-key": chave,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                method="POST",
            )

            try:
                with urllib.request.urlopen(req, timeout=15) as res:
                    if res.status == 200:
                        return True, f"Chave validada com sucesso via modelo '{modelo_alvo}'!"
                    return False, f"Resposta inesperada da API da Anthropic: HTTP {res.status}"
            except urllib.error.HTTPError as http_err:
                if http_err.code in (401, 403):
                    return False, "API Key da Anthropic inválida ou não autorizada. Verifique a chave digitada no console da Anthropic."
                if http_err.code == 404:
                    return False, f"Modelo '{modelo_alvo}' não encontrado ou indisponível na sua conta da Anthropic."
                if http_err.code == 429:
                    return False, "Limite de taxa/cota excedido na Anthropic (HTTP 429). Verifique seu saldo ou limite no console."
                corpo_erro = http_err.read().decode("utf-8", errors="ignore")
                return False, f"Erro HTTP {http_err.code} da Anthropic: {http_err.reason} ({corpo_erro[:150]})"
            except Exception as e:
                return False, f"Falha de conexão com a API da Anthropic: {e}"

        except Exception as exc:
            msg = str(exc)
            if "authentication_error" in msg.lower() or "401" in msg or "invalid_api_key" in msg.lower():
                return False, "API Key da Anthropic inválida. Verifique sua chave no Console da Anthropic."
            if "rate_limit_error" in msg.lower() or "429" in msg:
                return False, "Limite de cota ou taxa excedido na Anthropic (HTTP 429)."
            if "not_found_error" in msg.lower() or "404" in msg:
                return False, f"Modelo '{modelo_alvo}' não disponível para sua conta Anthropic."
            return False, f"Erro ao comunicar com a Anthropic: {msg}"

    def gerar_insights_chat(
        self,
        prompt_usuario: str,
        mensagens: list[dict],
        historico: list[dict] | None = None,
        tipo_analise: str | None = None,
        grupo_nome: str | None = None,
    ) -> tuple[bool, str]:
        """
        Executa a geração de insights via Anthropic (Claude) unindo o prompt do usuário/template analítico
        com o bloco de mensagens do banco SQLite no período filtrado.
        """
        chave = (self.api_key or os.getenv("ANTHROPIC_API_KEY", "")).strip()
        if not chave:
            return False, "Chave de API da Anthropic não configurada. Insira sua chave no card de configuração."

        modelo_alvo = (self.model or "claude-3-5-sonnet-20241022").strip()
        nome_grupo_str = grupo_nome or "Comunidade WhatsApp"

        # Formata o bloco de mensagens reais do período
        contexto_mensagens = formatar_contexto_mensagens(mensagens)
        total_msgs = len(mensagens)

        # Se houver template de análise útil pré-programada, utiliza o prompt especializado
        instrucao_principal = prompt_usuario.strip()
        if tipo_analise and tipo_analise in ANALISES_PRE_PROGRAMADAS:
            instrucao_principal = ANALISES_PRE_PROGRAMADAS[tipo_analise]["prompt_template"]

        # Montagem da mensagem do usuário com contexto
        mensagem_conteudo = f"""---
### DADOS DO GRUPO ANALISADO
- **Grupo:** {nome_grupo_str}
- **Total de Mensagens no Período Selecionado:** {total_msgs}

---
### HISTÓRICO DE MENSAGENS DO PERÍODO SELECIONADO:
\"\"\"
{contexto_mensagens}
\"\"\"

---
### SOLICITAÇÃO DO USUÁRIO / TAREFA ANALÍTICA:
{instrucao_principal}
"""

        # Monta a lista de mensagens para a API Anthropic (deve alternar user e assistant)
        mensagens_api = []
        if historico and isinstance(historico, list):
            for turno in historico[-6:]:
                role = "assistant" if turno.get("role") == "assistant" else "user"
                content = (turno.get("content") or "").strip()
                if content:
                    mensagens_api.append({"role": role, "content": content})

        mensagens_api.append({"role": "user", "content": mensagem_conteudo})

        try:
            import anthropic

            client = anthropic.Anthropic(api_key=chave)
            response = client.messages.create(
                model=modelo_alvo,
                max_tokens=4096,
                temperature=0.4,
                system=SYSTEM_PROMPT_BASE,
                messages=mensagens_api,
            )

            # Extrai o texto da resposta
            partes_texto = []
            for block in response.content:
                if getattr(block, "type", None) == "text":
                    partes_texto.append(block.text)
                elif hasattr(block, "text"):
                    partes_texto.append(str(block.text))

            texto_resposta = "\n".join(partes_texto).strip()
            if not texto_resposta:
                return False, "O modelo Claude não retornou conteúdo para esta consulta."

            return True, texto_resposta

        except ImportError:
            # Fallback HTTP REST robusto
            import urllib.error
            import urllib.request

            url = "https://api.anthropic.com/v1/messages"
            payload = json.dumps({
                "model": modelo_alvo,
                "max_tokens": 4096,
                "temperature": 0.4,
                "system": SYSTEM_PROMPT_BASE,
                "messages": mensagens_api,
            }).encode("utf-8")

            req = urllib.request.Request(
                url,
                data=payload,
                headers={
                    "x-api-key": chave,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                method="POST",
            )

            try:
                with urllib.request.urlopen(req, timeout=90) as res:
                    if res.status == 200:
                        data = json.loads(res.read().decode("utf-8"))
                        conteudos = data.get("content", [])
                        textos = [b.get("text", "") for b in conteudos if b.get("type") == "text"]
                        texto_final = "\n".join(textos).strip()
                        if texto_final:
                            return True, texto_final
                        return False, "Nenhuma resposta de texto gerada pela Anthropic."
                    return False, f"Resposta inesperada da API: HTTP {res.status}"
            except urllib.error.HTTPError as http_err:
                detalhe = http_err.read().decode("utf-8", errors="ignore")
                return False, f"Erro da API Anthropic (HTTP {http_err.code}): {http_err.reason} ({detalhe[:200]})"
            except Exception as e:
                return False, f"Falha de conexão com a API da Anthropic: {e}"

        except Exception as exc:
            msg = str(exc)
            if "authentication_error" in msg.lower() or "401" in msg:
                return False, "Chave de API da Anthropic inválida. Verifique sua chave no card de configuração."
            if "rate_limit_error" in msg.lower() or "429" in msg:
                return False, "Limite de cota de requisições excedido na Anthropic (HTTP 429). Aguarde alguns instantes."
            return False, f"Erro ao processar consulta com Anthropic (Claude): {msg}"


# Alias para retrocompatibilidade
GeminiService = AnthropicService


