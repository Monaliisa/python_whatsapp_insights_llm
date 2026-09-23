"""
Módulo de Relatórios Mensais — WhatsApp Insights LLM
Filosofia: SOLID, Clean Architecture e Bring Your Own Key (BYOK)

Este serviço encapsula o cálculo estatístico e quantitativo dos históricos de conversas
para geração de relatórios mensais estruturados em 4 seções fundamentais:
1. Saúde da Comunidade (Framework 3 Eixos: Volume/Rotina, Moderação e P2P)
2. Engajamento & Volumetria
3. Pontos de Atenção & Clima
4. Resumo Executivo & Ações Estratégicas (Consolidado com IA e exportação em HTML autônomo)
"""

from __future__ import annotations

import html
import json
import logging
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from services.anonymizer import get_anonymizer_service
from services.paths import get_db_path
from services.storage import count_messages, detect_active_group_from_db, get_app_state

logger = logging.getLogger(__name__)

# Palavras-chave associadas a dúvidas, dificuldades, termos críticos e pontos de atenção
TERMOS_ALERTA_PADRAO = [
    "duvida", "dúvida", "socorro", "erro", "bug", "travou", "falha",
    "problema", "nao consigo", "não consigo", "dificuldade", "travado",
    "nao funciona", "não funciona", "urgente", "alguem sabe", "alguém sabe"
]

# Expressões indicativas de Provas Sociais e Conquistas de Alunos
TERMOS_PROVA_SOCIAL = [
    "passei", "consegui", "aprovado", "aprovada", "contratado", "contratada",
    "estágio", "estagio", "primeiro emprego", "transição de carreira", "transicao",
    "novo trabalho", "fui chamado", "fui chamada", "projeto no ar", "freela",
    "primeiro freela", "aumento", "gratidão", "agradeço", "agradecer", "recomendo a alura",
    "aprendi de verdade", "portfólio", "portfolio"
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


def _render_markdown_simples(md_text: str) -> str:
    """Converte Markdown simples em HTML seguro com tipografia limpa."""
    if not md_text:
        return ""
    
    linhas = md_text.replace("\r\n", "\n").split("\n")
    out = []
    em_lista = False

    for linha in linhas:
        l = linha.strip()
        if not l:
            if em_lista:
                out.append("</ul>")
                em_lista = False
            continue

        # Cabeçalhos
        if l.startswith("### "):
            if em_lista:
                out.append("</ul>")
                em_lista = False
            texto = html.escape(l[4:])
            texto = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", texto)
            out.append(f"<h3 style='margin:18px 0 6px;color:#38bdf8;font-size:16px;'>{texto}</h3>")
            continue
        if l.startswith("## "):
            if em_lista:
                out.append("</ul>")
                em_lista = False
            texto = html.escape(l[3:])
            texto = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", texto)
            out.append(f"<h2 style='margin:22px 0 8px;color:#93c5fd;font-size:18px;border-bottom:1px solid #334155;padding-bottom:4px;'>{texto}</h2>")
            continue

        # Citações
        if l.startswith(">"):
            if em_lista:
                out.append("</ul>")
                em_lista = False
            texto = html.escape(l[1:].strip())
            texto = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", texto)
            out.append(f"<blockquote style='margin:10px 0;padding:8px 14px;border-left:3px solid #38bdf8;background:rgba(56,189,248,0.08);border-radius:0 6px 6px 0;color:#cbd5e1;font-style:italic;'>{texto}</blockquote>")
            continue

        # Listas
        if l.startswith(("- ", "* ", "• ")):
            if not em_lista:
                out.append("<ul style='margin:8px 0 12px 20px;padding-left:8px;'>")
                em_lista = True
            texto = html.escape(l[2:])
            texto = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", texto)
            out.append(f"<li style='margin:4px 0;color:#e2e8f0;'>{texto}</li>")
            continue

        # Parágrafos normais
        if em_lista:
            out.append("</ul>")
            em_lista = False

        texto = html.escape(l)
        texto = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", texto)
        texto = re.sub(r"\*(.+?)\*", r"<em>\1</em>", texto)
        out.append(f"<p style='margin:6px 0 10px;line-height:1.6;color:#e2e8f0;'>{texto}</p>")

    if em_lista:
        out.append("</ul>")

    return "\n".join(out)


class ReportService:
    """
    Serviço centralizado de geração e cálculo de métricas para relatórios mensais.
    """

    @classmethod
    def listar_meses_disponiveis(
        cls,
        db_path: str | None = None,
        grupo_id: str | None = None,
        grupo_nome: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Retorna a lista de meses disponíveis na base de dados SQLite com suas contagens,
        filtrando opcionalmente pelo grupo ativo.
        Ordenado cronologicamente do mais recente para o mais antigo.
        """
        if db_path is None:
            db_path = get_db_path()

        if not grupo_id and not grupo_nome:
            app_state = get_app_state()
            grupo_id = app_state.get("active_group_id")
            grupo_nome = app_state.get("active_group_name")

        conn = sqlite3.connect(db_path)
        cur = conn.cursor()

        query = "SELECT data_hora_ts, data_hora FROM messages WHERE data_hora_ts > 0"
        params: list[Any] = []
        if grupo_id or grupo_nome:
            filtros = []
            if grupo_id:
                filtros.append("grupo_id = ?")
                params.append(grupo_id)
            if grupo_nome:
                filtros.append("grupo_nome = ?")
                params.append(grupo_nome)
            query += f" AND ({' OR '.join(filtros)})"

        query += " ORDER BY data_hora_ts DESC"

        try:
            cur.execute(query, params)
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
        grupo_id: str | None = None,
        grupo_nome: str | None = None,
    ) -> dict[str, Any]:
        """
        Calcula as métricas completas para um determinado mês ('YYYY-MM'),
        incorporando o Framework dos 3 Eixos de Saúde Comunitária:
        1. Volume & Regularidade (rotina vs. concentração em picos de <= 3 dias)
        2. Dependência da Moderação/Equipe
        3. Conversas entre Pares (P2P e threads orgânicas)
        """
        if db_path is None:
            db_path = get_db_path()

        if not grupo_id and not grupo_nome:
            app_state = get_app_state()
            grupo_id = app_state.get("active_group_id")
            grupo_nome = app_state.get("active_group_name")

        meses_disp = cls.listar_meses_disponiveis(db_path, grupo_id=grupo_id, grupo_nome=grupo_nome)
        if not meses_disp:
            return {
                "tem_dados": False,
                "mensagem": "Nenhuma mensagem encontrada para este grupo na base de dados.",
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

        query_msgs = """
            SELECT id, data_hora, data_hora_ts, remetente, texto, is_reply,
                   reply_author, reply_text, has_attachments, grupo_nome
            FROM messages
            WHERE data_hora_ts >= ? AND data_hora_ts < ?
        """
        params_msgs: list[Any] = [ts_inicio, ts_fim]
        if grupo_id or grupo_nome:
            filtros_g = []
            if grupo_id:
                filtros_g.append("grupo_id = ?")
                params_msgs.append(grupo_id)
            if grupo_nome:
                filtros_g.append("grupo_nome = ?")
                params_msgs.append(grupo_nome)
            query_msgs += f" AND ({' OR '.join(filtros_g)})"

        query_msgs += " ORDER BY data_hora_ts ASC"

        cur.execute(query_msgs, params_msgs)
        rows = cur.fetchall()

        nome_grupo = grupo_nome or "Comunidade WhatsApp"
        if not grupo_nome:
            active_group = detect_active_group_from_db(db_path)
            if active_group:
                nome_grupo = active_group.get("nome") or active_group.get("id") or "Comunidade WhatsApp"

        conn.close()

        total_historico = count_messages(db_path, grupo_id=grupo_id, grupo_nome=grupo_nome)
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
        # PROCESSAMENTO ESTATÍSTICO & SANITIZAÇÃO
        # -----------------------------------------------------------------
        import calendar

        anon_svc = get_anonymizer_service()

        participantes_counter: Counter = Counter()
        mensagens_por_dia: dict[str, int] = defaultdict(int)
        mensagens_por_dia_semana: dict[int, int] = defaultdict(int)
        mensagens_por_periodo: dict[str, int] = {"Madrugada": 0, "Manhã": 0, "Tarde": 0, "Noite": 0}
        mensagens_por_hora: dict[int, int] = {h: 0 for h in range(24)}
        mensagens_por_semana: dict[str, int] = defaultdict(int)

        total_replies = 0
        total_anexos = 0
        total_texto_puro = 0
        soma_tamanho_texto = 0

        possiveis_duvidas = []
        possiveis_provas_sociais = []
        termos_alerta_encontrados: Counter = Counter()
        replies_entre_participantes: Counter = Counter()

        for idx, row in enumerate(rows):
            mid, dh_str, ts, remetente, texto, is_reply, reply_author, reply_text, has_att, _ = row
            texto_str = (texto or "").strip()
            remetente_real = (remetente or "Participante").strip()
            remetente_pseu = anon_svc.pseudonimizar_remetente(remetente_real)

            participantes_counter[remetente_pseu] += 1

            if is_reply:
                total_replies += 1
                if reply_author:
                    autor_reply_pseu = anon_svc.pseudonimizar_remetente(reply_author)
                    if autor_reply_pseu != remetente_pseu:
                        replies_entre_participantes[f"{remetente_pseu}->{autor_reply_pseu}"] += 1

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
                mensagens_por_hora[hora] += 1

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

            # Detecção de provas sociais / conquistas
            for termo_ps in TERMOS_PROVA_SOCIAL:
                if termo_ps in texto_lower and len(texto_str) > 15:
                    texto_sanitizado = anon_svc.redigir_texto(texto_str)
                    possiveis_provas_sociais.append({
                        "id": mid,
                        "autor": remetente_pseu,
                        "data_hora": dh_str,
                        "termo_gatilho": termo_ps,
                        "texto": texto_sanitizado[:220] + ("..." if len(texto_sanitizado) > 220 else ""),
                    })
                    break

            if "?" in texto_str and len(texto_str) > 10:
                texto_sanitizado = anon_svc.redigir_texto(texto_str)
                possiveis_duvidas.append({
                    "id": mid,
                    "remetente": remetente_pseu,
                    "texto": texto_sanitizado[:160] + ("..." if len(texto_sanitizado) > 160 else ""),
                    "data_hora": dh_str,
                })

        # -----------------------------------------------------------------
        # CÁLCULO DOS 3 EIXOS DE SAÚDE COMUNITÁRIA
        # -----------------------------------------------------------------
        total_participantes = len(participantes_counter)
        dias_ativos = len(mensagens_por_dia)
        media_diaria = round(total_mensagens / dias_ativos, 1) if dias_ativos > 0 else 0
        media_por_participante = round(total_mensagens / total_participantes, 1) if total_participantes > 0 else 0
        taxa_respostas = round((total_replies / total_mensagens) * 100, 1) if total_mensagens > 0 else 0

        # Eixo 1: Volume & Concentração em Picos
        top_dias_ordenados = sorted(mensagens_por_dia.values(), reverse=True)
        top_3_dias_vol = sum(top_dias_ordenados[:3])
        pct_top_3_dias = round((top_3_dias_vol / total_mensagens) * 100, 1) if total_mensagens > 0 else 0
        concentrado_em_pico = pct_top_3_dias >= 50.0 and total_mensagens >= 20

        # Eixo 2: Dependência da Moderação / Concentração do Top Participante
        top_participante_qtd = top_dias_ordenados[0] if top_dias_ordenados else 0
        top_1_membro_volume = participantes_counter.most_common(1)[0][1] if participantes_counter else 0
        pct_top_1_membro = round((top_1_membro_volume / total_mensagens) * 100, 1) if total_mensagens > 0 else 0
        dependencia_moderacao_falha = pct_top_1_membro >= 35.0 and total_participantes > 2

        # Eixo 3: Conversa entre Pares (P2P)
        threads_organicas_estimadas = len(replies_entre_participantes)
        p2p_falha = threads_organicas_estimadas < 2 and total_mensagens >= 20

        # Classificação Geral de Saúde
        eixos_falhando = 0
        if concentrado_em_pico:
            eixos_falhando += 1
        if dependencia_moderacao_falha:
            eixos_falhando += 1
        if p2p_falha:
            eixos_falhando += 1

        if eixos_falhando >= 2 or total_mensagens < 5:
            saude_status = "critico"
            saude_label = "Crítico"
            saude_cor = "#ef4444"
            saude_justificativa = f"{eixos_falhando} eixos em não conformidade. Baixa autonomia ou engajamento crítico."
        elif eixos_falhando == 1 or concentrado_em_pico:
            saude_status = "atencao"
            saude_label = "Atenção"
            saude_cor = "#f59e0b"
            if concentrado_em_pico:
                saude_justificativa = f"{pct_top_3_dias}% das mensagens em apenas 3 dias — volume concentrado em pico, sem rotina diária."
            elif dependencia_moderacao_falha:
                saude_justificativa = f"Top membro/moderação concentra {pct_top_1_membro}% das mensagens do período (dependência elevada)."
            else:
                saude_justificativa = "Poucas trocas diretas entre pares observadas no período."
        else:
            saude_status = "saudavel"
            saude_label = "Saudável"
            saude_cor = "#10b981"
            saude_justificativa = "Nenhum eixo falha: cadência diária ativa, autonomia de membros e conversas fluidas entre pares."

        saude_comunidade_data = {
            "status": saude_status,
            "label": saude_label,
            "cor": saude_cor,
            "justificativa": saude_justificativa,
            "eixos": [
                {
                    "nome": "Volume & Regularidade",
                    "medida": f"{media_diaria} msgs/dia ({pct_top_3_dias}% nos 3 maiores dias)",
                    "aprovado": not concentrado_em_pico,
                    "status_label": "Pico Concentrado" if concentrado_em_pico else "Rotina Regular",
                },
                {
                    "nome": "Dependência da Moderação",
                    "medida": f"Top 1 participante concentra {pct_top_1_membro}% do volume",
                    "aprovado": not dependencia_moderacao_falha,
                    "status_label": "Alta Dependência" if dependencia_moderacao_falha else "Comunidade Autônoma",
                },
                {
                    "nome": "Conversa entre Pares (P2P)",
                    "medida": f"{threads_organicas_estimadas} pares de troca ({taxa_respostas}% replies)",
                    "aprovado": not p2p_falha,
                    "status_label": "Baixa Troca P2P" if p2p_falha else "Trocas Orgânicas Ativas",
                },
            ],
        }

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

        num_dias_mes = calendar.monthrange(ano, mes_num)[1]
        evolucao_diaria = []
        for d in range(1, num_dias_mes + 1):
            dia_iso = f"{ano:04d}-{mes_num:02d}-{d:02d}"
            dia_fmt = f"{d:02d}/{mes_num:02d}"
            total_dia = mensagens_por_dia.get(dia_iso, 0)
            evolucao_diaria.append({
                "dia": dia_iso,
                "dia_label": dia_fmt,
                "dia_numero": d,
                "total": total_dia,
            })

        distribuicao_horaria = [
            {"hora": h, "label": f"{h:02d}h", "total": mensagens_por_hora[h]}
            for h in range(24)
        ]

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
            "distribuicao_horaria": distribuicao_horaria,
            "evolucao_diaria": evolucao_diaria,
        }

        # -----------------------------------------------------------------
        # PONTOS DE ATENÇÃO & TERMOS
        # -----------------------------------------------------------------
        total_alertas_termos = sum(termos_alerta_encontrados.values())
        top_termos_alerta = [
            {"termo": t, "ocorrencias": c}
            for t, c in termos_alerta_encontrados.most_common(6)
        ]
        participantes_isolados = sum(1 for count in participantes_counter.values() if count == 1)

        pontos_atencao_data = {
            "nivel_atencao": saude_label,
            "nivel_atencao_cor": saude_cor,
            "nivel_atencao_desc": saude_justificativa,
            "total_duvidas_mapeadas": len(possiveis_duvidas),
            "amostra_duvidas": possiveis_duvidas[:8],
            "total_provas_sociais": len(possiveis_provas_sociais),
            "amostra_provas_sociais": possiveis_provas_sociais[:6],
            "total_termos_alerta": total_alertas_termos,
            "top_termos_alerta": top_termos_alerta,
            "participantes_isolados": participantes_isolados,
        }

        # -----------------------------------------------------------------
        # ANÁLISES QUANTITATIVAS & TOP PARTICIPANTES
        # -----------------------------------------------------------------
        top_participantes = []
        for remetente_pseu, qtd in participantes_counter.most_common(10):
            pct = round((qtd / total_mensagens) * 100, 1) if total_mensagens > 0 else 0
            # Mapeia papel preliminar
            papel = "mentor-informal" if qtd >= 20 and total_participantes > 5 else "engajado"
            top_participantes.append({
                "remetente": remetente_pseu,
                "total_mensagens": qtd,
                "percentual": pct,
                "papel": papel,
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
                "percentual_texto_puro": round((total_texto_puro / total_mensagens) * 100, 1) if total_mensagens > 0 else 0,
                "percentual_anexos": round((total_anexos / total_mensagens) * 100, 1) if total_mensagens > 0 else 0,
                "percentual_replies": round((total_replies / total_mensagens) * 100, 1) if total_mensagens > 0 else 0,
            },
            "top_participantes": top_participantes,
            "distribuicao_semanal": distribuicao_semanal_lista,
        }

        # -----------------------------------------------------------------
        # RESUMO EXECUTIVO DEFAULT
        # -----------------------------------------------------------------
        top_membros_str = ", ".join([f"**{p['remetente']}** ({p['total_mensagens']} msgs)" for p in top_participantes[:3]]) or "Nenhum"
        resumo_executivo_padrao = f"""### 📊 Relatório Executivo de Inteligência Comunitária — {nome_grupo} ({_formatar_label_mes(mes_alvo)})

**1. Diagnóstico de Saúde Comunitária ({saude_label}):**
{saude_justificativa}
- **Volume no Mês:** {total_mensagens:,} mensagens ({media_diaria} msgs/dia ao longo de {dias_ativos} dias ativos).
- **Concentração de Picos:** {pct_top_3_dias}% do volume concentrado nos 3 dias de maior atividade (Pico em {dia_pico_formatado}).
- **Dinâmica de Trocas:** {taxa_respostas}% de respostas encadeadas e {threads_organicas_estimadas} pares de interação P2P mapeados.

**2. Lideranças e Principais Contribuidores:**
Os membros de maior destaque no período foram: {top_membros_str}.

**3. Gargalos Pedagógicos & Provas Sociais:**
- **Dúvidas e Dificuldades:** {len(possiveis_duvidas)} perguntas mapeadas e {total_alertas_termos} ocorrências de termos críticos/dúvidas.
- **Provas Sociais / Conquistas:** {len(possiveis_provas_sociais)} mensagens com termos de conquista, transição ou depoimento identificadas.

> *Dica: Clique no botão "✨ Gerar Resumo com Claude AI" acima para gerar o diagnóstico qualitativo detalhado com a inteligência artificial.*
""".replace(",", ".")

        return {
            "tem_dados": True,
            "grupo_nome": nome_grupo,
            "mes_id": mes_alvo,
            "mes_label": _formatar_label_mes(mes_alvo),
            "meses_disponiveis": meses_disp,
            "saude_comunidade": saude_comunidade_data,
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
        Gera um Resumo Executivo inteligente e qualitativo via Anthropic (Claude)
        com base nas regras do framework comunitário.
        """
        from services.llm import AnthropicService

        if not api_key:
            return False, "Chave de API da Anthropic não fornecida. Configure sua chave no card de configuração."

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
        saude = metricas.get("saude_comunidade", {})
        eng = metricas.get("engajamento", {})
        atencao = metricas.get("pontos_atencao", {})
        quant = metricas.get("analises_quantitativas", {})

        prompt_executivo = f"""Você é o ZapInsights AI, especialista em inteligência de dados e diagnóstico de comunidades de aprendizagem.
Você deve redigir o **RESUMO EXECUTIVO MENSAL** para o grupo **{nome_grupo}**, referente a **{mes_label}**, aplicando rigorosamente a metodologia dos 3 eixos de saúde.

---
### DADOS CONSOLIDADOS DO MÊS ({mes_label}):
- **Diagnóstico Geral de Saúde:** {saude.get('label', '-')} ({saude.get('justificativa', '-')})
- **Total de Mensagens no Mês:** {eng.get('total_mensagens', 0)}
- **Participantes Ativos:** {eng.get('total_participantes', 0)}
- **Dias com Atividade:** {eng.get('dias_ativos', 0)} dias (Média: {eng.get('media_mensagens_dia', 0)} msgs/dia)
- **Concentração de Picos:** {saude.get('eixos', [{}])[0].get('medida', '-')}
- **Taxa de Interações/Replies (P2P):** {eng.get('taxa_respostas_percent', 0)}%
- **Possíveis Dúvidas & Termos de Alerta:** {atencao.get('total_duvidas_mapeadas', 0)} dúvidas / {atencao.get('total_termos_alerta', 0)} termos críticos
- **Provas Sociais / Conquistas Detectadas:** {atencao.get('total_provas_sociais', 0)}
- **Top Participantes:** {json.dumps(quant.get('top_participantes', [])[:5], ensure_ascii=False)}

---
### ESTRUTURA OBRIGATÓRIA DO RELATÓRIO EXECUTIVO (MARKDOWN):

## 📑 Resumo Executivo Mensal — {mes_label}

### 1. 🩺 Diagnóstico de Saúde Comunitária (3 Eixos)
Avalie objetivamente os 3 eixos:
- **Eixo 1 (Volume & Regularidade):** Houve rotina diária ou pico concentrado?
- **Eixo 2 (Autonomia vs. Moderação):** A comunidade conversa sozinha ou depende da equipe?
- **Eixo 3 (Conversa entre Pares / P2P):** Qual o nível de ajuda mútua entre alunos?

### 2. 🔥 Temas Centrais & Termômetro de Discussões
Classifique os assuntos que mais geraram debate em **Quentes** (threads longas), **Mornos** (menções) e **Lacunas** identificadas.

### 3. 👥 Lideranças Informais & Provas Sociais
Aponte os membros que atuaram como `mentor-informal`, os alunos em ascensão e cite trechos de conquistas/vitórias relatadas no grupo em bloco `>`.

### 4. ⚠️ Gargalos Pedagógicos & Dores do Grupo
Mapeie as principais dúvidas técnicas e pontos de atrito identificados.

### 5. 🎯 Recomendações Práticas para a Moderação / Coordenação
Proponha 3 a 5 ações objetivas para o próximo ciclo (ex.: reforço de tópicos quentes, intervenção em gargalos, acolhimento de membros silenciosos).
"""

        service = AnthropicService(api_key=api_key, model=model or "claude-3-5-sonnet-20241022")
        return service.gerar_insights_chat(
            prompt_usuario=prompt_executivo,
            mensagens=mensagens_estruturadas,
            grupo_nome=nome_grupo,
        )

    @classmethod
    def gerar_relatorio_html(
        cls,
        mes: str,
        metricas: dict[str, Any],
        resumo_conteudo: str | None = None,
    ) -> str:
        """
        Compila e gera um Relatório HTML Standalone (arquivo único autônomo, offline,
        com visual executivo profissional, Dark/Light theme, cards de KPIs, gráficos SVG nativos,
        tabelas e resumo executivo formatado).
        """
        grupo_nome = metricas.get("grupo_nome") or "Comunidade WhatsApp"
        mes_label = metricas.get("mes_label") or _formatar_label_mes(mes)
        saude = metricas.get("saude_comunidade", {})
        eng = metricas.get("engajamento", {})
        atencao = metricas.get("pontos_atencao", {})
        quant = metricas.get("analises_quantitativas", {})

        texto_resumo_md = resumo_conteudo or metricas.get("resumo_executivo", {}).get("texto_padrao", "")
        resumo_html = _render_markdown_simples(texto_resumo_md)

        # Gráfico SVG de Timeline Diária
        evolucao = eng.get("evolucao_diaria", [])
        svg_bars = []
        max_val = max([item.get("total", 0) for item in evolucao] or [1])
        if max_val == 0:
            max_val = 1

        svg_width = 800
        svg_height = 160
        bar_gap = 4
        n_bars = max(len(evolucao), 1)
        bar_w = max(4, (svg_width - (n_bars * bar_gap)) / n_bars)

        for i, pt in enumerate(evolucao):
            val = pt.get("total", 0)
            dia_lbl = pt.get("dia_label", "")
            h_bar = int((val / max_val) * 120)
            x = int(i * (bar_w + bar_gap) + 10)
            y = svg_height - h_bar - 25

            cor_bar = "#3b82f6" if val > 0 else "#334155"
            svg_bars.append(
                f'<rect x="{x}" y="{y}" width="{int(bar_w)}" height="{max(h_bar, 2)}" rx="3" fill="{cor_bar}">'
                f'<title>{dia_lbl}: {val} msgs</title></rect>'
            )
            if i % 3 == 0 or i == len(evolucao) - 1:
                svg_bars.append(
                    f'<text x="{x + int(bar_w/2)}" y="{svg_height - 6}" font-size="9" fill="#94a3b8" text-anchor="middle">{dia_lbl}</text>'
                )

        svg_timeline_html = f"""
        <svg viewBox="0 0 {svg_width} {svg_height}" width="100%" height="{svg_height}" style="overflow:visible;">
            <line x1="0" y1="{svg_height - 22}" x2="{svg_width}" y2="{svg_height - 22}" stroke="#334155" stroke-width="1" />
            {"".join(svg_bars)}
        </svg>
        """

        # Linhas da tabela de Top Participantes
        top_parts_rows = []
        for idx, p in enumerate(quant.get("top_participantes", [])[:10], 1):
            top_parts_rows.append(
                f"<tr>"
                f"<td style='padding:8px 12px;color:#94a3b8;'>{idx}</td>"
                f"<td style='padding:8px 12px;font-weight:600;color:#f1f5f9;'>{html.escape(p.get('remetente', ''))}</td>"
                f"<td style='padding:8px 12px;'><span class='badge' style='background:rgba(59,130,246,0.2);color:#93c5fd;font-size:11px;'>{p.get('papel', 'engajado')}</span></td>"
                f"<td style='padding:8px 12px;text-align:right;font-weight:700;color:#38bdf8;'>{p.get('total_mensagens', 0)}</td>"
                f"<td style='padding:8px 12px;text-align:right;color:#94a3b8;'>{p.get('percentual', 0)}%</td>"
                f"</tr>"
            )

        top_parts_html = "\n".join(top_parts_rows) or "<tr><td colspan='5' style='text-align:center;padding:12px;color:#64748b;'>Sem dados de participantes</td></tr>"

        # Tags de termos de alerta
        termos_tags = []
        for t in atencao.get("top_termos_alerta", []):
            termos_tags.append(
                f"<span style='background:rgba(239,68,68,0.15);border:1px solid rgba(239,68,68,0.3);color:#fca5a5;padding:4px 10px;border-radius:20px;font-size:12px;font-weight:600;'>"
                f"{html.escape(t.get('termo', ''))} ({t.get('ocorrencias', 0)}x)</span>"
            )
        termos_tags_html = " ".join(termos_tags) or "<span style='color:#64748b;font-size:12px;'>Nenhum termo de alerta recorrente.</span>"

        # Cards dos 3 eixos
        eixos_cards_html = []
        for e in saude.get("eixos", []):
            cor_st = "#10b981" if e.get("aprovado") else "#f59e0b"
            bg_st = "rgba(16,185,129,0.15)" if e.get("aprovado") else "rgba(245,158,11,0.15)"
            eixos_cards_html.append(
                f"""
                <div style="background:#111827;border:1px solid #1e293b;border-radius:8px;padding:12px 14px;">
                    <div style="font-size:11px;color:#94a3b8;font-weight:700;text-transform:uppercase;">{html.escape(e.get('nome', ''))}</div>
                    <div style="margin:6px 0;font-size:13px;color:#f1f5f9;font-weight:600;">{html.escape(e.get('medida', ''))}</div>
                    <span style="display:inline-block;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:700;background:{bg_st};color:{cor_st};">
                        {html.escape(e.get('status_label', ''))}
                    </span>
                </div>
                """
            )

        html_template = f"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Relatório Mensal — {html.escape(grupo_nome)} ({html.escape(mes_label)})</title>
  <style>
    :root {{
      --bg: #0b1120;
      --card: #0f172a;
      --border: #1e293b;
      --text: #f8fafc;
      --muted: #94a3b8;
      --primary: #2563eb;
      --accent: #38bdf8;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }}
    body {{ background: var(--bg); color: var(--text); padding: 32px 20px; line-height: 1.5; }}
    .container {{ max-width: 960px; margin: 0 auto; }}
    .header {{ border-bottom: 1px solid var(--border); padding-bottom: 20px; margin-bottom: 24px; display: flex; justify-content: space-between; align-items: flex-start; flex-wrap: wrap; gap: 14px; }}
    .title {{ font-size: 24px; font-weight: 800; color: #f1f5f9; }}
    .subtitle {{ font-size: 13px; color: var(--muted); margin-top: 4px; }}
    .card {{ background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 20px; margin-bottom: 20px; }}
    .card-title {{ font-size: 16px; font-weight: 700; color: #93c5fd; margin-bottom: 12px; display: flex; align-items: center; gap: 8px; }}
    .kpi-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin-bottom: 16px; }}
    .kpi-card {{ background: #111827; border: 1px solid #1e293b; border-radius: 8px; padding: 12px 14px; }}
    .kpi-label {{ font-size: 11px; color: var(--muted); text-transform: uppercase; font-weight: 700; }}
    .kpi-value {{ font-size: 20px; font-weight: 800; color: #f1f5f9; margin-top: 4px; }}
    .kpi-sub {{ font-size: 11px; color: #64748b; margin-top: 2px; }}
    .badge {{ display: inline-block; padding: 3px 10px; border-radius: 999px; font-size: 12px; font-weight: 700; text-transform: uppercase; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; margin-top: 8px; }}
    th {{ background: #1e293b; color: #93c5fd; text-align: left; padding: 8px 12px; font-weight: 600; }}
    tr:nth-child(even) {{ background: rgba(30, 41, 59, 0.3); }}
    @media print {{
      body {{ background: #ffffff; color: #0f172a; }}
      .card {{ background: #ffffff; border-color: #cbd5e1; color: #0f172a; }}
      .kpi-card {{ background: #f8fafc; border-color: #cbd5e1; color: #0f172a; }}
      .title, .card-title {{ color: #0f172a !important; }}
      th {{ background: #f1f5f9; color: #1e293b; }}
    }}
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <div>
        <div class="title">📊 Relatório Mensal de Comunidade</div>
        <div class="subtitle">{html.escape(grupo_nome)} • Período: <strong>{html.escape(mes_label)}</strong></div>
      </div>
      <div>
        <span class="badge" style="background:{saude.get('cor', '#10b981')}22;color:{saude.get('cor', '#10b981')};border:1px solid {saude.get('cor', '#10b981')}55;font-size:14px;padding:6px 14px;">
          Saúde: {html.escape(saude.get('label', 'Saudável'))}
        </span>
      </div>
    </div>

    <!-- 1. RESUMO EXECUTIVO -->
    <div class="card" style="background: linear-gradient(180deg, #131d33 0%, #0f172a 100%); border-color: #2563eb55;">
      <div class="card-title" style="color: #60a5fa;">📝 1. Resumo Executivo & Diagnóstico Estratégico</div>
      <div style="background: #0b1120; border: 1px solid #1e293b; border-radius: 8px; padding: 18px; margin-top: 10px;">
        {resumo_html}
      </div>
    </div>

    <!-- 2. SAÚDE EM 3 EIXOS -->
    <div class="card">
      <div class="card-title">🩺 2. Framework dos 3 Eixos de Saúde Comunitária</div>
      <div style="font-size: 13px; color: var(--muted); margin-bottom: 14px;">
        {html.escape(saude.get('justificativa', ''))}
      </div>
      <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px;">
        {"".join(eixos_cards_html)}
      </div>
    </div>

    <!-- 3. ENGAJAMENTO E TIMELINE -->
    <div class="card">
      <div class="card-title">📈 3. Engajamento & Volumetria</div>
      <div class="kpi-grid">
        <div class="kpi-card">
          <div class="kpi-label">Total de Mensagens</div>
          <div class="kpi-value" style="color:#38bdf8;">{eng.get('total_mensagens', 0)}</div>
          <div class="kpi-sub">no período</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-label">Participantes Ativos</div>
          <div class="kpi-value" style="color:#a78bfa;">{eng.get('total_participantes', 0)}</div>
          <div class="kpi-sub">membros</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-label">Média por Dia Ativo</div>
          <div class="kpi-value" style="color:#34d399;">{eng.get('media_mensagens_dia', 0)}</div>
          <div class="kpi-sub">msgs / dia</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-label">Taxa de Resposta</div>
          <div class="kpi-value" style="color:#fbbf24;">{eng.get('taxa_respostas_percent', 0)}%</div>
          <div class="kpi-sub">mensagens encadeadas</div>
        </div>
      </div>

      <div style="margin-top: 18px;">
        <div style="font-size: 13px; font-weight: 700; color: #93c5fd; margin-bottom: 8px;">Evolução Diária de Mensagens</div>
        <div style="background: #0b1120; border: 1px solid #1e293b; border-radius: 8px; padding: 14px 10px;">
          {svg_timeline_html}
        </div>
      </div>
    </div>

    <!-- 4. PONTOS DE ATENÇÃO & LIDERANÇAS -->
    <div class="card">
      <div class="card-title">⚠️ 4. Pontos de Atenção & Lideranças Comunitárias</div>
      <div style="margin-bottom: 14px;">
        <div style="font-size: 12px; font-weight: 700; color: var(--muted); margin-bottom: 8px;">🔥 Termos Críticos Identificados:</div>
        <div>{termos_tags_html}</div>
      </div>

      <div style="margin-top: 18px;">
        <div style="font-size: 13px; font-weight: 700; color: #93c5fd; margin-bottom: 8px;">🏆 Top Participantes Mais Ativos</div>
        <div style="background: #0b1120; border: 1px solid #1e293b; border-radius: 8px; overflow: auto;">
          <table>
            <thead>
              <tr>
                <th style="width: 30px;">#</th>
                <th>Membro / Remetente</th>
                <th>Papel Estimado</th>
                <th style="text-align: right;">Mensagens</th>
                <th style="text-align: right;">% Mês</th>
              </tr>
            </thead>
            <tbody>
              {top_parts_html}
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <div style="text-align: center; font-size: 12px; color: #64748b; margin-top: 30px; border-top: 1px solid #1e293b; padding-top: 14px;">
      ZapInsights LLM • Relatório gerado automaticamente em {datetime.now().strftime("%d/%m/%Y às %H:%M")}
    </div>
  </div>
</body>
</html>
"""
        return html_template
