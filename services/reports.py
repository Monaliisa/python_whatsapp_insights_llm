"""
Módulo de Relatórios Mensais — WhatsApp Insights LLM
Filosofia: SOLID, Clean Architecture e Bring Your Own Key (BYOK)

Este serviço encapsula o cálculo estatístico e quantitativo dos históricos de conversas
para geração de relatórios mensais estruturados em 4 seções:
1. Engajamento
2. Pontos de Atenção
3. Análises Quantitativas
4. Resumo Executivo
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from services.llm import GeminiService
from services.paths import get_db_path
from services.storage import detect_active_group_from_db

logger = logging.getLogger(__name__)

# Palavras-chave associadas a dúvidas, dificuldades, termos críticos e pontos de atenção
TERMOS_ALERTA_PADRAO = [
    "duvida", "dúvida", "ajuda", "socorro", "erro", "bug", "travou", "falha",
    "problema", "nao consigo", "não consigo", "dificuldade", "travado",
    "nao funciona", "não funciona", "urgente", "alguem sabe", "alguém sabe"
]

NOMES_MESES = {
    1: "Janeiro", 2: "Fevereiro", 3: "Março", 4: "Abril",
    5: "Maio", 6: "Junho", 7: "Julho", 8: "Agosto",
    9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro"
}

DIAS_SEMANA = {
    0: "Segunda", 1: "Terça", 2: "Quarta", 3: "Quinta",
    4: "Sexta", 5: "Sábado", 6: "Domingo"
}


def _formatar_label_mes(ano_mes: str) -> str:
    """Converte 'YYYY-MM' em 'Mês / AAAA' (ex: '2026-03' -> 'Março / 2026')."""
    try:
        partes = ano_mes.split("-")
        ano = int(partes[0])
        mes = int(partes[1])
        nome = NOMES_MESES.get(mes, str(mes))
        return f"{nome} / {ano}"
    except Exception:
        return ano_mes


class ReportService:
    """
    Serviço centralizado de geração e cálculo de métricas para relatórios mensais.
    """

    @classmethod
    def listar_meses_disponiveis(cls, db_path: str | None = None) -> list[dict[str, Any]]:
        """
        Retorna a lista de meses disponíveis na base de dados SQLite com suas contagens.
        Ordenado cronologicamente do mais recente para o mais antigo.
        """
        if db_path is None:
            db_path = get_db_path()

        conn = sqlite3.connect(db_path)
        cur = conn.cursor()

        try:
            cur.execute(
                """
                SELECT data_hora_ts, data_hora
                FROM messages
                WHERE data_hora_ts > 0
                ORDER BY data_hora_ts DESC
                """
            )
            rows = cur.fetchall()
        except sqlite3.OperationalError:
            conn.close()
            return []

        conn.close()

        if not rows:
            return []

        contagem_meses: dict[str, int] = Counter()
        for row in rows:
            ts = row[0]
            try:
                dt = datetime.fromtimestamp(ts)
                chave = dt.strftime("%Y-%m")
                contagem_meses[chave] += 1
            except Exception:
                continue

        resultado = []
        for ano_mes, total in sorted(contagem_meses.items(), key=lambda x: x[0], reverse=True):
            resultado.append({
                "id": ano_mes,
                "label": _formatar_label_mes(ano_mes),
                "total_mensagens": total,
            })

        return resultado

    @classmethod
    def calcular_metricas_mensais(
        cls,
        mes: str | None = None,
        db_path: str | None = None,
    ) -> dict[str, Any]:
        """
        Calcula as métricas completas para um determinado mês ('YYYY-MM').
        Se mes for None, seleciona automaticamente o mês mais recente disponível.
        """
        if db_path is None:
            db_path = get_db_path()

        meses_disp = cls.listar_meses_disponiveis(db_path)
        if not meses_disp:
            return {
                "tem_dados": False,
                "mensagem": "Nenhuma mensagem encontrada na base de dados.",
                "meses_disponiveis": [],
            }

        mes_alvo = mes
        if not mes_alvo or not any(m["id"] == mes_alvo for m in meses_disp):
            mes_alvo = meses_disp[0]["id"]

        try:
            partes = mes_alvo.split("-")
            ano = int(partes[0])
            mes_num = int(partes[1])
            dt_inicio = datetime(ano, mes_num, 1, 0, 0, 0)
            if mes_num == 12:
                dt_fim = datetime(ano + 1, 1, 1, 0, 0, 0)
            else:
                dt_fim = datetime(ano, mes_num + 1, 1, 0, 0, 0)

            ts_inicio = dt_inicio.timestamp()
            ts_fim = dt_fim.timestamp()
        except Exception:
            return {
                "tem_dados": False,
                "mensagem": f"Formato de mês inválido: '{mes}'. Use 'YYYY-MM'.",
                "meses_disponiveis": meses_disp,
            }

        conn = sqlite3.connect(db_path)
        cur = conn.cursor()

        cur.execute(
            """
            SELECT id, data_hora, data_hora_ts, remetente, texto, is_reply,
                   reply_author, reply_text, has_attachments, grupo_nome
            FROM messages
            WHERE data_hora_ts >= ? AND data_hora_ts < ?
            ORDER BY data_hora_ts ASC
            """,
            (ts_inicio, ts_fim),
        )
        rows = cur.fetchall()

        total_historico = cur.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        active_group = detect_active_group_from_db(db_path)
        nome_grupo = (active_group.get("nome") or active_group.get("id")) if active_group else "Comunidade WhatsApp"

        conn.close()

        total_mensagens = len(rows)
        if total_mensagens == 0:
            return {
                "tem_dados": False,
                "grupo_nome": nome_grupo,
                "mes_id": mes_alvo,
                "mes_label": _formatar_label_mes(mes_alvo),
                "mensagem": "Nenhuma mensagem registrada no mês selecionado.",
                "meses_disponiveis": meses_disp,
            }

        # -----------------------------------------------------------------
        # PROCESSAMENTO ESTATÍSTICO
        # -----------------------------------------------------------------
        participantes_counter: Counter = Counter()
        mensagens_por_dia: dict[str, int] = defaultdict(int)
        mensagens_por_dia_semana: dict[int, int] = defaultdict(int)
        mensagens_por_periodo: dict[str, int] = {"Madrugada": 0, "Manhã": 0, "Tarde": 0, "Noite": 0}
        mensagens_por_semana: dict[str, int] = defaultdict(int)

        total_replies = 0
        total_anexos = 0
        total_texto_puro = 0
        soma_tamanho_texto = 0

        possiveis_duvidas = []
        termos_alerta_encontrados: Counter = Counter()

        for idx, row in enumerate(rows):
            mid, dh_str, ts, remetente, texto, is_reply, reply_author, reply_text, has_att, _ = row
            texto_str = (texto or "").strip()
            remetente_str = (remetente or "Participante").strip()

            participantes_counter[remetente_str] += 1

            if is_reply:
                total_replies += 1
            if has_att:
                total_anexos += 1
            if not has_att and not is_reply:
                total_texto_puro += 1

            soma_tamanho_texto += len(texto_str)

            try:
                dt = datetime.fromtimestamp(ts)
                dia_str = dt.strftime("%Y-%m-%d")
                mensagens_por_dia[dia_str] += 1
                mensagens_por_dia_semana[dt.weekday()] += 1

                hora = dt.hour
                if 0 <= hora < 6:
                    mensagens_por_periodo["Madrugada"] += 1
                elif 6 <= hora < 12:
                    mensagens_por_periodo["Manhã"] += 1
                elif 12 <= hora < 18:
                    mensagens_por_periodo["Tarde"] += 1
                else:
                    mensagens_por_periodo["Noite"] += 1

                semana_mes = f"Semana {((dt.day - 1) // 7) + 1}"
                mensagens_por_semana[semana_mes] += 1
            except Exception:
                pass

            texto_lower = texto_str.lower()
            for termo in TERMOS_ALERTA_PADRAO:
                if termo in texto_lower:
                    termos_alerta_encontrados[termo] += 1

            if "?" in texto_str and len(texto_str) > 10:
                possiveis_duvidas.append({
                    "id": mid,
                    "remetente": remetente_str,
                    "texto": texto_str[:160] + ("..." if len(texto_str) > 160 else ""),
                    "data_hora": dh_str,
                })

        # -----------------------------------------------------------------
        # 1. SEÇÃO ENGAJAMENTO
        # -----------------------------------------------------------------
        total_participantes = len(participantes_counter)
        dias_ativos = len(mensagens_por_dia)
        media_diaria = round(total_mensagens / dias_ativos, 1) if dias_ativos > 0 else 0
        media_por_participante = round(total_mensagens / total_participantes, 1) if total_participantes > 0 else 0
        taxa_respostas = round((total_replies / total_mensagens) * 100, 1) if total_mensagens > 0 else 0

        dia_pico_str, dia_pico_qtd = ("", 0)
        if mensagens_por_dia:
            dia_pico_str, dia_pico_qtd = max(mensagens_por_dia.items(), key=lambda x: x[1])
            try:
                dt_pico = datetime.strptime(dia_pico_str, "%Y-%m-%d")
                dia_pico_formatado = dt_pico.strftime("%d/%m/%Y")
            except Exception:
                dia_pico_formatado = dia_pico_str
        else:
            dia_pico_formatado = "N/D"

        periodo_predominante = max(mensagens_por_periodo.items(), key=lambda x: x[1])[0] if total_mensagens > 0 else "N/D"

        distribuicao_dias_semana = []
        for d_idx in range(7):
            distribuicao_dias_semana.append({
                "dia": DIAS_SEMANA[d_idx],
                "total": mensagens_por_dia_semana.get(d_idx, 0),
            })

        engajamento_data = {
            "total_mensagens": total_mensagens,
            "total_participantes": total_participantes,
            "dias_ativos": dias_ativos,
            "media_mensagens_dia": media_diaria,
            "media_mensagens_participante": media_por_participante,
            "taxa_respostas_percent": taxa_respostas,
            "dia_pico": dia_pico_formatado,
            "dia_pico_volume": dia_pico_qtd,
            "periodo_predominante": periodo_predominante,
            "periodos_distribuicao": mensagens_por_periodo,
            "dias_semana_distribuicao": distribuicao_dias_semana,
        }

        # -----------------------------------------------------------------
        # 2. SEÇÃO PONTOS DE ATENÇÃO
        # -----------------------------------------------------------------
        total_alertas_termos = sum(termos_alerta_encontrados.values())
        top_termos_alerta = [
            {"termo": t, "ocorrencias": c}
            for t, c in termos_alerta_encontrados.most_common(6)
        ]

        participantes_isolados = sum(1 for count in participantes_counter.values() if count == 1)

        score_atencao = 0
        if len(possiveis_duvidas) > 15:
            score_atencao += 2
        elif len(possiveis_duvidas) > 5:
            score_atencao += 1

        if total_alertas_termos > 20:
            score_atencao += 2
        elif total_alertas_termos > 8:
            score_atencao += 1

        if score_atencao >= 3:
            nivel_atencao = "Alta Atenção"
            nivel_atencao_cor = "#ef4444"
            nivel_atencao_desc = "Volume elevado de dúvidas e termos de dificuldade detectados no período. Recomendada intervenção da moderação."
        elif score_atencao >= 1:
            nivel_atencao = "Monitoramento Moderado"
            nivel_atencao_cor = "#f59e0b"
            nivel_atencao_desc = "Atividade estável com pontos de atenção pontuais em dúvidas específicas."
        else:
            nivel_atencao = "Clima Estável"
            nivel_atencao_cor = "#10b981"
            nivel_atencao_desc = "Fluxo de conversas saudável, sem gargalos críticos recorrentes identificados."

        pontos_atencao_data = {
            "nivel_atencao": nivel_atencao,
            "nivel_atencao_cor": nivel_atencao_cor,
            "nivel_atencao_desc": nivel_atencao_desc,
            "total_duvidas_mapeadas": len(possiveis_duvidas),
            "amostra_duvidas": possiveis_duvidas[:8],
            "total_termos_alerta": total_alertas_termos,
            "top_termos_alerta": top_termos_alerta,
            "participantes_isolados": participantes_isolados,
        }

        # -----------------------------------------------------------------
        # 3. SEÇÃO ANÁLISES QUANTITATIVAS
        # -----------------------------------------------------------------
        top_participantes = []
        for remetente_nome, qtd in participantes_counter.most_common(10):
            pct = round((qtd / total_mensagens) * 100, 1) if total_mensagens > 0 else 0
            top_participantes.append({
                "remetente": remetente_nome,
                "total_mensagens": qtd,
                "percentual": pct,
            })

        distribuicao_semanal_lista = []
        for sem in sorted(mensagens_por_semana.keys()):
            distribuicao_semanal_lista.append({
                "semana": sem,
                "total": mensagens_por_semana[sem],
                "percentual": round((mensagens_por_semana[sem] / total_mensagens) * 100, 1) if total_mensagens > 0 else 0,
            })

        tamanho_medio_texto = round(soma_tamanho_texto / total_mensagens, 1) if total_mensagens > 0 else 0
        pct_historico = round((total_mensagens / total_historico) * 100, 1) if total_historico > 0 else 100

        analises_quantitativas_data = {
            "total_geral_mes": total_mensagens,
            "total_historico_base": total_historico,
            "percentual_do_historico": pct_historico,
            "tamanho_medio_caracteres": tamanho_medio_texto,
            "tipos_mensagens": {
                "texto_puro": total_texto_puro,
                "anexos_midia": total_anexos,
                "replies_citacoes": total_replies,
                "percentual_anexos": round((total_anexos / total_mensagens) * 100, 1) if total_mensagens > 0 else 0,
                "percentual_replies": round((total_replies / total_mensagens) * 100, 1) if total_mensagens > 0 else 0,
            },
            "top_participantes": top_participantes,
            "distribuicao_semanal": distribuicao_semanal_lista,
        }

        # -----------------------------------------------------------------
        # 4. SEÇÃO RESUMO EXECUTIVO (CONSOLIDADO AUTOMÁTICO DEFAULT)
        # -----------------------------------------------------------------
        top_membros_str = ", ".join([f"**{p['remetente']}** ({p['total_mensagens']} msgs)" for p in top_participantes[:3]]) or "Nenhum"
        resumo_executivo_padrao = f"""### 📊 Relatório Executivo Consolidado — {nome_grupo} ({_formatar_label_mes(mes_alvo)})

**1. Panorama Geral e Engajamento:**
No mês de **{_formatar_label_mes(mes_alvo)}**, foram registradas **{total_mensagens:,} mensagens** distribuídas entre **{total_participantes} participantes ativos** ao longo de **{dias_ativos} dias** de atividade. A média de engajamento foi de **{media_diaria} mensagens/dia**, com uma taxa de resposta de **{taxa_respostas}%**, indicando boa dinamicidade nas trocas. O pico de atividade ocorreu em **{dia_pico_formatado}** ({dia_pico_qtd} mensagens).

**2. Principais Contribuidores:**
Os membros de maior destaque no período foram: {top_membros_str}.

**3. Diagnóstico e Pontos de Atenção:**
O clima geral da comunidade foi classificado como **{nivel_atencao}**. Foram identificadas **{len(possiveis_duvidas)} possíveis dúvidas em aberto** e **{total_alertas_termos} ocorrências de termos de dificuldade/suporte**, demandando atenção pontual dos moderadores para garantir alinhamento.

> *Dica: Clique no botão "✨ Gerar Resumo com Gemini AI" acima para gerar uma análise qualitativa aprofundada com inteligência artificial sobre os tópicos reais debatidos.*
""".replace(",", ".")

        return {
            "tem_dados": True,
            "grupo_nome": nome_grupo,
            "mes_id": mes_alvo,
            "mes_label": _formatar_label_mes(mes_alvo),
            "meses_disponiveis": meses_disp,
            "engajamento": engajamento_data,
            "pontos_atencao": pontos_atencao_data,
            "analises_quantitativas": analises_quantitativas_data,
            "resumo_executivo": {
                "texto_padrao": resumo_executivo_padrao,
            },
        }

    @classmethod
    def gerar_resumo_executivo_llm(
        cls,
        api_key: str,
        model: str,
        mes: str,
        metricas: dict[str, Any],
        db_path: str | None = None,
    ) -> tuple[bool, str]:
        """
        Gera um Resumo Executivo inteligente e qualitativo via Google Gemini BYOK.
        """
        if not api_key:
            return False, "Chave de API do Google Gemini não fornecida. Configure sua chave BYOK."

        if db_path is None:
            db_path = get_db_path()

        try:
            partes = mes.split("-")
            ano = int(partes[0])
            mes_num = int(partes[1])
            dt_inicio = datetime(ano, mes_num, 1, 0, 0, 0)
            if mes_num == 12:
                dt_fim = datetime(ano + 1, 1, 1, 0, 0, 0)
            else:
                dt_fim = datetime(ano, mes_num + 1, 1, 0, 0, 0)

            ts_inicio = dt_inicio.timestamp()
            ts_fim = dt_fim.timestamp()
        except Exception:
            return False, f"Formato de mês inválido: '{mes}'"

        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, data_hora, remetente, texto, is_reply, reply_author, reply_text, has_attachments
            FROM messages
            WHERE data_hora_ts >= ? AND data_hora_ts < ?
            ORDER BY data_hora_ts ASC
            LIMIT 1200
            """,
            (ts_inicio, ts_fim),
        )
        rows = cur.fetchall()
        conn.close()

        mensagens_estruturadas = []
        for r in rows:
            mensagens_estruturadas.append({
                "id": r[0],
                "data_hora": r[1],
                "remetente": r[2],
                "texto": r[3],
                "is_reply": r[4],
                "reply_author": r[5],
                "reply_text": r[6],
                "has_attachments": r[7],
            })

        nome_grupo = metricas.get("grupo_nome") or "Comunidade WhatsApp"
        mes_label = metricas.get("mes_label") or mes
        eng = metricas.get("engajamento", {})
        atencao = metricas.get("pontos_atencao", {})
        quant = metricas.get("analises_quantitativas", {})

        prompt_executivo = f"""Você é o ZapInsights AI, especialista em análise estratégica de dados comunitários.
Você deve redigir o **RESUMO EXECUTIVO CONSOLIDADO** do relatório mensal para o grupo **{nome_grupo}**, referente ao mês de **{mes_label}**.

---
### DADOS ESTATÍSTICOS DO MÊS ({mes_label}):
- **Total de Mensagens no Mês:** {eng.get('total_mensagens', 0)}
- **Participantes Ativos:** {eng.get('total_participantes', 0)}
- **Dias Ativos no Mês:** {eng.get('dias_ativos', 0)} dias
- **Média Diária:** {eng.get('media_mensagens_dia', 0)} msgs/dia
- **Taxa de Interações/Replies:** {eng.get('taxa_respostas_percent', 0)}%
- **Dia de Pico:** {eng.get('dia_pico', '-')} ({eng.get('dia_pico_volume', 0)} mensagens)
- **Diagnóstico de Atenção:** {atencao.get('nivel_atencao', '-')}
- **Possíveis Dúvidas Mapeadas:** {atencao.get('total_duvidas_mapeadas', 0)}
- **Termos de Alerta/Gargalos:** {atencao.get('total_termos_alerta', 0)}
- **Top Participantes:** {json.dumps(quant.get('top_participantes', [])[:5], ensure_ascii=False)}

---
### INSTRUÇÕES DE ESTRUTURAÇÃO DO RESUMO EXECUTIVO:
Elabore um relatório executivo profissional e direto ao ponto com a seguinte estrutura em Markdown:

## 📑 Resumo Executivo Mensal — {mes_label}

### 1. 🌟 Destaques e Trajetória do Mês
Sintetize a movimentação geral da comunidade no mês, os tópicos centrais debatidos e momentos marcantes.

### 2. 👥 Dinâmica Comunitária & Engajamento
Analise a participação dos membros, identificando lideranças naturais, interajuda e momentos de maior interesse.

### 3. ⚠️ Gargalos Pedagógicos & Pontos Críticos
Aponte as principais dores, dificuldades técnicas/conceituais levantadas e tópicos que geraram insegurança ou atrito.

### 4. 🎯 Recomendações Estratégicas para a Gestão / Moderação
Proponha 3 a 5 ações práticas (ex.: novos materiais, lives de reforço, orientações no grupo) para o próximo ciclo mensal.

Utilize tom profissional, analítico, acolhedor e com formatação rica em Markdown (tópicos, negritos e destaques).
"""

        service = GeminiService(api_key=api_key, model=model or "gemini-2.5-flash")
        return service.gerar_insights_chat(
            prompt_usuario=prompt_executivo,
            mensagens=mensagens_estruturadas,
            grupo_nome=nome_grupo,
        )
