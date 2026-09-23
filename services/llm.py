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
        "id": "claude-3-7-sonnet-20250219",
        "nome": "Claude 3.7 Sonnet",
        "descricao": "Modelo mais avançado da família Claude com alta precisão e capacidades híbridas de raciocínio.",
        "tipo": "avancado",
        "recomendado": False,
    },
    {
        "id": "claude-3-haiku-20240307",
        "nome": "Claude 3 Haiku",
        "descricao": "Velocidade ultra rápida, excelente custo-benefício e ampla disponibilidade.",
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

from services.anonymizer import get_anonymizer_service

# System Prompt base enriquecido com o Framework de Análise de Comunidades WhatsApp
SYSTEM_PROMPT_BASE = """Você é o ZapInsights AI, um assistente especialista em inteligência de dados, análise qualitativa e gestão estratégica de comunidades e grupos do WhatsApp.

Sua missão é processar mensagens reais extraídas de conversas e fornecer diagnósticos analíticos precisos, sínteses claras e planos de ação objetivos para gestores, educadores e moderadores, utilizando um framework rigoroso de análise comunitária.

CRITÉRIOS E REGRAS ANALÍTICAS DA METODOLOGIA:
1. **Saúde do Grupo em 3 Eixos**:
   - **Eixo 1: Volume & Regularidade**: Avalie se o volume de mensagens representa um fluxo diário consistente ou se concentra mais de 50% das mensagens em apenas 1 a 3 dias (pico pontual isolado, não rotina).
   - **Eixo 2: Dependência da Moderação/Equipe**: Meça se a comunidade depende excessivamente dos administradores para falar (falha se >= 30% das mensagens vêm da equipe) ou se possui autonomia.
   - **Eixo 3: Conversa entre Pares (P2P)**: Identifique se há trocas orgânicas entre os próprios participantes (threads com 5+ mensagens entre membros sem intervenção direta de admin). Menos de 2 threads no período indica falha comunitária.
   - **Classificação**: `saudavel` (nenhum eixo falha), `atencao` (1 a 2 eixos falham ou volume concentrado em pico) ou `critico` (3 eixos falham, grupo silencioso ou 100% dependente da equipe).

2. **Temperatura e Recorrência dos Temas**:
   - `quente`: Temas que geraram debate engajado no período (threads de 5+ mensagens no mesmo assunto).
   - `morno`: Assuntos mencionados esporadicamente ou em mensagens soltas.
   - `esfriando`: Tópicos que deixaram de ser debatidos (decay de relevância).

3. **Mapeamento de Personas e Lideranças Comunitárias**:
   - `mentor-informal`: Membro veterano ou muito colaborativo que acolhe colegas, tira dúvidas e compartilha projetos.
   - `detrator`: Membro com reclamações acionáveis sobre metodologia, plataforma ou mercado (risco de churn). Registre a dor específica de forma construtiva.
   - `iniciante-em-ascensao`: Aluno/membro novo com curva rápida de evolução e alto engajamento.
   - `engajado`: Participante frequente e motivador.

4. **Provas Sociais e Histórias de Sucesso**:
   - Extraia citações espontâneas de conquistas (aprovação em vagas, transição de carreira, primeiros freelas, projetos no ar, elogios sinceros à metodologia).

5. **Referências de Mercado, Ferramentas e Cursos**:
   - Mapeie ferramentas, bibliotecas, canais e cursos externos/concorrentes citados com classificação de sentimento (`positivo`, `misto`, `negativo`, `neutro`).

DIRETRIZES DE RESPOSTA E APRESENTAÇÃO:
- **Fidelidade estrita aos dados**: Baseie-se apenas nas mensagens do contexto. Se algo não constar, declare explicitamente.
- **Formatação Markdown Rica**: Utilize títulos estruturados, tabelas comparativas, listas e citações em bloco (`>`).
- **Privacidade por Padrão**: Sempre utilize os pseudônimos dos remetentes (ex: `aluno-0042`) e nunca deduza dados pessoais reais.
- **Tom de Voz**: Analítico, consultivo, executivo, acolhedor e focado em decisões práticas. Responda em Português do Brasil.
"""

# Templates especializados para Análises Úteis
ANALISES_PRE_PROGRAMADAS = {
    "saude_comunidade": {
        "id": "saude_comunidade",
        "titulo": "Diagnóstico de Saúde Comunitária (3 Eixos)",
        "icone": "🩺",
        "badge": "Saúde & Eixos",
        "descricao": "Avaliação de saúde nos 3 eixos: Volume/Rotina, Dependência da Moderação e Conversa entre Pares (P2P).",
        "frase_chat": "Faça um diagnóstico completo de saúde da comunidade com base nos 3 eixos fundamentais.",
        "prompt_template": """Elabore um **Diagnóstico de Saúde Comunitária** avaliando os 3 eixos metodológicos no período selecionado:

1. **🩺 Classificação de Saúde do Grupo**:
   - Diagnóstico geral: `saudavel`, `atencao` ou `critico`.
   - **Eixo 1 (Volume & Rotina)**: Análise da cadência de mensagens. O engajamento foi rotineiro ou concentrado em picos isolados?
   - **Eixo 2 (Dependência da Moderação)**: Qual o grau de autonomia dos membros vs. dependência da equipe/admins?
   - **Eixo 3 (Conversa entre Pares / P2P)**: Existiram threads orgânicas entre alunos (5+ msgs)? Quantas foram identificadas?

2. **📊 Tabela Resumo dos Eixos**:
   | Eixo | Situação Observada | Status (Aprovado / Atenção / Falha) |
   |---|---|---|

3. **👥 Lideranças Informais e Dinâmica Social**:
   - Identifique quem são os membros que atuaram como `mentor-informal` ou `iniciante-em-ascensao`.

4. **🎯 Recomendações de Ação**:
   - 3 recomendações claras para os moderadores equilibrarem a dinâmica no próximo ciclo.
""",
    },
    "temas_temperatura": {
        "id": "temas_temperatura",
        "titulo": "Termômetro de Temas (Quente / Morno / Esfriando)",
        "icone": "🔥",
        "badge": "Temas & Tendências",
        "descricao": "Mapeamento dos assuntos debatidos categorizados por temperatura e engajamento das discussões.",
        "frase_chat": "Mapeie os temas debatidos no período e classifique-os por temperatura (Quente, Morno, Esfriando).",
        "prompt_template": """Faça um **Mapeamento de Temas e Tendências** a partir das conversas do período:

1. **🔥 Temas Quentes (Threads de 5+ mensagens)**:
   - Liste os temas que geraram debates intensos e engajados, detalhando o contexto e o sentimento do grupo.

2. **🌤️ Temas Mornos (Menções esporádicas ou tópicos pontuais)**:
   - Assuntos citados sem formação de grandes discussões.

3. **❄️ Temas Esfriando / Lacunas de Conteúdo**:
   - Dúvidas que ficaram sem continuidade ou assuntos recorrentes em ciclos anteriores que perderam tração.

4. **📊 Tabela Sintética de Temas**:
   | Tema | Temperatura | Volume Relativo | Resumo do Debate |
   |---|---|---|---|

5. **💡 Oportunidades Pedagógicas**: Conteúdos ou eventos que poderiam ser criados para atender à demanda dos temas quentes.
""",
    },
    "provas_sociais_mentores": {
        "id": "provas_sociais_mentores",
        "titulo": "Provas Sociais, Conquistas & Mentores",
        "icone": "🌟",
        "badge": "Sucesso & Lideranças",
        "descricao": "Extração de relatos de sucesso, conquistas de vagas, transições e lideranças informais.",
        "frase_chat": "Identifique as provas sociais, conquistas e os principais mentores informais do grupo.",
        "prompt_template": """Realize uma busca aprofundada por **Provas Sociais, Histórias de Sucesso e Lideranças Comunitárias** no período:

1. **🏆 Provas Sociais e Conquistas de Alunos**:
   - Relatos de contratação, estágio, transição de carreira, primeiros freelas, projetos publicados ou elogios à metodologia.
   - Para cada prova social, inclua a citação fiel (usando o pseudônimo do autor) em bloco `>`.

2. **🤝 Mentores Informais & Membros em Ascensão**:
   - Membros que se destacaram pelo acolhimento, suporte técnico e ajuda voluntária aos colegas.

3. **⚠️ Voz Crítica / Alertas de Detratores**:
   - Pontos de frustração legítimos levantados por alunos experientes (com foco em melhoria contínua de produto/conteúdo).

4. **🔗 Ferramentas e Referências Externas Mais Elogiadas**:
   - Tecnologias, canais, livros ou concorrentes citados positivamente pelos participantes.
""",
    },
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


def formatar_contexto_mensagens(mensagens: list[dict], max_chars: int = 400000, anonimizar: bool = True) -> str:
    """
    Formata a lista de mensagens extraídas do SQLite em um formato de log estruturado,
    enxuto e sanitizado com LGPD/Privacidade por padrão para o consumo seguro de contexto pela LLM.
    """
    if not mensagens:
        return "Nenhuma mensagem encontrada no período selecionado."

    if anonimizar:
        anon_svc = get_anonymizer_service()
        mensagens_proc, _ = anon_svc.anonimizar_mensagens(mensagens)
    else:
        mensagens_proc = mensagens

    linhas = []
    chars_acumulados = 0

    for msg in mensagens_proc:
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
    def listar_modelos_conta(cls, api_key: str) -> list[dict[str, Any]]:
        """Consulta a API da Anthropic para obter os modelos ativos e acessíveis para esta chave."""
        chave = api_key.strip()
        if not chave:
            return list(MODELOS_DISPONIVEIS)

        try:
            import anthropic

            client = anthropic.Anthropic(api_key=chave)
            page = client.models.list(limit=50)
            modelos = []
            for item in page.data:
                mid = getattr(item, "id", "")
                dname = getattr(item, "display_name", mid) or mid
                if mid:
                    modelos.append({
                        "id": mid,
                        "nome": dname,
                        "descricao": f"Modelo ativo na sua conta Anthropic ({mid})",
                        "tipo": "balanceado" if "sonnet" in mid.lower() else ("leve" if "haiku" in mid.lower() else "avancado"),
                        "recomendado": "sonnet" in mid.lower(),
                    })
            if modelos:
                return modelos
        except Exception:
            pass

        # Fallback HTTP REST caso o SDK não retorne
        try:
            import json
            import urllib.request

            req = urllib.request.Request(
                "https://api.anthropic.com/v1/models",
                headers={
                    "x-api-key": chave,
                    "anthropic-version": "2023-06-01",
                },
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=10) as res:
                if res.status == 200:
                    data = json.loads(res.read().decode("utf-8"))
                    modelos = []
                    for item in data.get("data", []):
                        mid = item.get("id", "")
                        dname = item.get("display_name", mid) or mid
                        if mid:
                            modelos.append({
                                "id": mid,
                                "nome": dname,
                                "descricao": f"Modelo ativo na sua conta Anthropic ({mid})",
                                "tipo": "balanceado" if "sonnet" in mid.lower() else ("leve" if "haiku" in mid.lower() else "avancado"),
                                "recomendado": "sonnet" in mid.lower(),
                            })
                    if modelos:
                        return modelos
        except Exception:
            pass

        return list(MODELOS_DISPONIVEIS)

    @classmethod
    def listar_modelos(cls) -> list[dict[str, Any]]:
        """Retorna a lista de modelos recomendados e suas características."""
        return list(MODELOS_DISPONIVEIS)

    @classmethod
    def listar_analises_uteis(cls) -> list[dict[str, Any]]:
        """Retorna a lista estruturada de análises úteis para popular a interface."""
        return list(ANALISES_PRE_PROGRAMADAS.values())

    def validar_api_key(
        self,
        api_key: str | None = None,
        model: str | None = None,
    ) -> tuple[bool, str, list[dict[str, Any]], str]:
        """
        Testa a validade da API Key informada fazendo uma chamada de teste com descoberta dinâmica de modelos.
        Retorna (sucesso: bool, mensagem: str, modelos_disponiveis: list, modelo_resolvido: str).
        """
        chave = (api_key or self.api_key or os.getenv("ANTHROPIC_API_KEY", "")).strip()
        modelo_alvo = (model or self.model or "claude-3-5-sonnet-20241022").strip()

        if not chave:
            return False, "Nenhuma API Key informada. Forneça sua chave da Anthropic (iniciada com 'sk-ant-').", [], modelo_alvo

        # 1. Tenta listar modelos disponíveis diretamente da conta
        modelos_conta = self.listar_modelos_conta(chave)
        ids_disponiveis = [m["id"] for m in modelos_conta if "id" in m]

        # Se o modelo alvo não estiver disponível na conta, seleciona automaticamente o melhor disponível
        if ids_disponiveis and modelo_alvo not in ids_disponiveis:
            candidatos_sonnet = [i for i in ids_disponiveis if "sonnet" in i.lower()]
            candidatos_haiku = [i for i in ids_disponiveis if "haiku" in i.lower()]
            candidatos_opus = [i for i in ids_disponiveis if "opus" in i.lower()]
            if candidatos_sonnet:
                modelo_alvo = candidatos_sonnet[0]
            elif candidatos_haiku:
                modelo_alvo = candidatos_haiku[0]
            elif candidatos_opus:
                modelo_alvo = candidatos_opus[0]
            else:
                modelo_alvo = ids_disponiveis[0]

        try:
            import anthropic

            client = anthropic.Anthropic(api_key=chave)
            client.messages.create(
                model=modelo_alvo,
                max_tokens=10,
                messages=[{"role": "user", "content": "Responda apenas 'OK'."}],
            )

            return True, f"Chave validada com sucesso via modelo '{modelo_alvo}'! Conexão ativa com a Anthropic.", modelos_conta, modelo_alvo

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
                        return True, f"Chave validada com sucesso via modelo '{modelo_alvo}'!", modelos_conta, modelo_alvo
                    return False, f"Resposta inesperada da API da Anthropic: HTTP {res.status}", modelos_conta, modelo_alvo
            except urllib.error.HTTPError as http_err:
                if http_err.code in (401, 403):
                    return False, "API Key da Anthropic inválida ou não autorizada. Verifique a chave digitada no console da Anthropic.", [], modelo_alvo
                if http_err.code == 404:
                    return False, f"Modelo '{modelo_alvo}' não encontrado na sua conta. Modelos disponíveis: {', '.join(ids_disponiveis[:5])}", modelos_conta, modelo_alvo
                if http_err.code == 429:
                    return False, "Limite de taxa/cota excedido na Anthropic (HTTP 429). Verifique seu saldo ou limite no console.", modelos_conta, modelo_alvo
                corpo_erro = http_err.read().decode("utf-8", errors="ignore")
                return False, f"Erro HTTP {http_err.code} da Anthropic: {http_err.reason} ({corpo_erro[:150]})", modelos_conta, modelo_alvo
            except Exception as e:
                return False, f"Falha de conexão com a API da Anthropic: {e}", modelos_conta, modelo_alvo

        except Exception as exc:
            msg = str(exc)
            if "not_found_error" in msg.lower() or "404" in msg:
                # Tenta fallback para outros modelos da conta
                try:
                    import anthropic
                    client = anthropic.Anthropic(api_key=chave)
                    for alt_model in ids_disponiveis:
                        if alt_model == modelo_alvo:
                            continue
                        try:
                            client.messages.create(
                                model=alt_model,
                                max_tokens=10,
                                messages=[{"role": "user", "content": "Responda apenas 'OK'."}],
                            )
                            return True, f"Chave validada com sucesso via modelo alternativo '{alt_model}' disponível na sua conta.", modelos_conta, alt_model
                        except Exception:
                            continue
                except Exception:
                    pass

                disp_str = ", ".join(ids_disponiveis[:6]) if ids_disponiveis else "nenhum modelo identificado"
                return False, f"Modelo '{modelo_alvo}' não disponível. Modelos encontrados na sua conta: {disp_str}", modelos_conta, modelo_alvo

            if "authentication_error" in msg.lower() or "401" in msg or "invalid_api_key" in msg.lower():
                return False, "API Key da Anthropic inválida. Verifique sua chave no Console da Anthropic.", [], modelo_alvo
            if "rate_limit_error" in msg.lower() or "429" in msg:
                return False, "Limite de cota ou taxa excedido na Anthropic (HTTP 429).", modelos_conta, modelo_alvo
            return False, f"Erro ao comunicar com a Anthropic: {msg}", modelos_conta, modelo_alvo

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
            try:
                response = client.messages.create(
                    model=modelo_alvo,
                    max_tokens=4096,
                    system=SYSTEM_PROMPT_BASE,
                    messages=mensagens_api,
                )
            except Exception as e:
                # Se o modelo não estiver disponível na conta, tenta outro modelo ativo
                if "not_found_error" in str(e).lower() or "404" in str(e):
                    modelos_conta = self.listar_modelos_conta(chave)
                    ids_disp = [m["id"] for m in modelos_conta if "id" in m and m["id"] != modelo_alvo]
                    response = None
                    for alt_id in ids_disp:
                        try:
                            response = client.messages.create(
                                model=alt_id,
                                max_tokens=4096,
                                system=SYSTEM_PROMPT_BASE,
                                messages=mensagens_api,
                            )
                            logger.info(f"Fallback bem-sucedido para o modelo '{alt_id}'")
                            break
                        except Exception:
                            continue
                    if response is None:
                        raise e
                else:
                    raise e

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


