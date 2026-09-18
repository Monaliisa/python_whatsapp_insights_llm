from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime
from pathlib import Path

# Garante inclusão do diretório raiz no PYTHONPATH
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.database import (
    get_database_url,
    init_database_tables,
    is_postgres,
    test_db_connection,
)
from services.storage import (
    count_messages,
    delete_catalog_group,
    delete_group_messages,
    fetch_messages_for_llm_range,
    get_db_path,
    get_message_date_bounds,
    init_db,
    load_catalog_groups_with_stats,
    save_messages,
    set_active_group,
)
from services.reports import ReportService


def run_isolation_tests():
    print("=" * 65)
    print(" TESTE DE ISOLAMENTO E PERSISTÊNCIA MULTI-GRUPO")
    print("=" * 65)

    tipo = "PostgreSQL (Neon)" if is_postgres() else "SQLite (Local)"
    print(f"-> Banco ativo: {tipo}")
    print(f"-> Target:      {get_db_path()}")
    print("-" * 65)

    db_path = get_db_path()
    init_db(db_path)

    grupo_a = f"Grupo_Alpha_{uuid.uuid4().hex[:6]}"
    grupo_b = f"Grupo_Beta_{uuid.uuid4().hex[:6]}"
    now = datetime.now()

    # 1. Inserir 3 mensagens no Grupo Alpha
    print(f"\n[1/5] Inserindo 3 mensagens no '{grupo_a}'...")
    msgs_a = [
        {
            "id": f"msg_a_{i}_{uuid.uuid4().hex[:6]}",
            "grupo_nome": grupo_a,
            "remetente": f"Membro Alpha {i}",
            "texto": f"Mensagem teste Alpha #{i}",
            "data_hora": now.strftime("%Y-%m-%d %H:%M:%S"),
        }
        for i in range(1, 4)
    ]
    salvas_a = save_messages(msgs_a, db_path=db_path)
    assert salvas_a == 3, f"Esperado 3 mensagens salvas, obtido {salvas_a}"
    print(f" [OK] {salvas_a} mensagens salvas no Grupo Alpha.")

    # 2. Inserir 2 mensagens no Grupo Beta
    print(f"\n[2/5] Inserindo 2 mensagens no '{grupo_b}'...")
    msgs_b = [
        {
            "id": f"msg_b_{i}_{uuid.uuid4().hex[:6]}",
            "grupo_nome": grupo_b,
            "remetente": f"Membro Beta {i}",
            "texto": f"Mensagem teste Beta #{i}",
            "data_hora": now.strftime("%Y-%m-%d %H:%M:%S"),
        }
        for i in range(1, 3)
    ]
    salvas_b = save_messages(msgs_b, db_path=db_path)
    assert salvas_b == 2, f"Esperado 2 mensagens salvas, obtido {salvas_b}"
    print(f" [OK] {salvas_b} mensagens salvas no Grupo Beta.")

    # 3. Validar Isolamento das Contagens e Catálogo
    print("\n[3/5] Validando isolamento de contagens e catálogo enriquecido...")
    cnt_a = count_messages(db_path, grupo_nome=grupo_a)
    cnt_b = count_messages(db_path, grupo_nome=grupo_b)
    print(f" -> Contagem Grupo Alpha: {cnt_a} (esperado: 3)")
    print(f" -> Contagem Grupo Beta:  {cnt_b} (esperado: 2)")
    assert cnt_a == 3, f"Erro: contagem de Alpha esperava 3, retornou {cnt_a}"
    assert cnt_b == 2, f"Erro: contagem de Beta esperava 2, retornou {cnt_b}"

    catalogo = load_catalog_groups_with_stats(db_path)
    stats_a = next((g for g in catalogo if g["nome"] == grupo_a), None)
    stats_b = next((g for g in catalogo if g["nome"] == grupo_b), None)
    assert stats_a and stats_a["total_mensagens"] == 3, "Catálogo não refletiu stats de Alpha"
    assert stats_b and stats_b["total_mensagens"] == 2, "Catálogo não refletiu stats de Beta"
    print(" [OK] Catálogo reflete contagens isoladas por grupo com precisão.")

    # 4. Validar Relatórios e LLM isolados por grupo
    print("\n[4/5] Testando filtros de Relatórios e LLM por grupo...")
    mes_atual = now.strftime("%Y-%m")
    rep_a = ReportService.calcular_metricas_mensais(mes=mes_atual, db_path=db_path, grupo_nome=grupo_a)
    rep_b = ReportService.calcular_metricas_mensais(mes=mes_atual, db_path=db_path, grupo_nome=grupo_b)

    assert rep_a.get("tem_dados"), "Relatório Alpha deveria ter dados"
    assert rep_b.get("tem_dados"), "Relatório Beta deveria ter dados"
    total_a = rep_a["analises_quantitativas"]["total_geral_mes"]
    total_b = rep_b["analises_quantitativas"]["total_geral_mes"]
    assert total_a == 3, f"Relatório Alpha deveria ter 3 msgs, tem {total_a}"
    assert total_b == 2, f"Relatório Beta deveria ter 2 msgs, tem {total_b}"

    msgs_llm_a = fetch_messages_for_llm_range(db_path=db_path, grupo_nome=grupo_a)
    assert len(msgs_llm_a) == 3, f"LLM Alpha deveria ter 3 msgs, obteve {len(msgs_llm_a)}"
    assert all(m["grupo_nome"] == grupo_a for m in msgs_llm_a), "Mensagens de Beta vazaram no contexto LLM de Alpha"
    print(" [OK] Relatórios e contexto LLM 100% isolados por grupo.")

    # 5. Testar Exclusão Seletiva (Excluir Alpha não toca em Beta)
    print(f"\n[5/5] Excluindo apenas o '{grupo_a}' e confirmando preservação de '{grupo_b}'...")
    del_a = delete_group_messages(grupo_a, db_path=db_path)
    assert del_a == 3, f"Deveria ter deletado 3 msgs de Alpha, deletou {del_a}"

    cnt_a_pos = count_messages(db_path, grupo_nome=grupo_a)
    cnt_b_pos = count_messages(db_path, grupo_nome=grupo_b)
    assert cnt_a_pos == 0, f"Alpha deveria estar com 0 mensagens, tem {cnt_a_pos}"
    assert cnt_b_pos == 2, f"Beta deveria continuar com 2 mensagens intactas, tem {cnt_b_pos}"
    print(" [OK] Grupo Alpha excluído sem afetar o Grupo Beta.")

    # Limpa dados de Beta e remove do catálogo para não poluir
    delete_group_messages(grupo_b, db_path=db_path)
    delete_catalog_group(grupo_a)
    delete_catalog_group(grupo_b)
    print(" [OK] Limpeza final de mensagens e catálogo concluída.")

    print("\n" + "=" * 65)
    print(" TODOS OS TESTES DE ISOLAMENTO MULTI-GRUPO PASSARAM COM SUCESSO!")
    print("=" * 65)
    return True


if __name__ == "__main__":
    sucesso = run_isolation_tests()
    sys.exit(0 if sucesso else 1)
