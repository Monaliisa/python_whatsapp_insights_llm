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
    get_db_session,
    init_database_tables,
    is_postgres,
    test_db_connection,
)
from services.models import Consulta, Message, ColetaHistorico


def run_tests():
    print("=" * 60)
    print(" TESTE DE CONEXÃO E PERSISTÊNCIA - WHATSAPP INSIGHTS")
    print("=" * 60)
    
    url = get_database_url()
    tipo = "PostgreSQL (Neon)" if is_postgres() else "SQLite (Local)"
    print(f"-> Tipo de Banco detectado: {tipo}")
    print(f"-> URL sanitizada:          {url.split('@')[-1] if '@' in url else url}")
    print("-" * 60)

    # 1. Teste de Conexão
    print("\n[1/4] Testando conectividade com o banco...")
    res = test_db_connection()
    if res.get("success"):
        print(f" [OK] {res.get('message')}")
    else:
        print(f" [ERRO] {res.get('message')}")
        return False

    # 2. Inicialização de Tabelas (DDL)
    print("\n[2/4] Criando e validando schema de tabelas...")
    try:
        init_database_tables()
        print(" [OK] Tabelas 'consultas', 'messages', 'coletas_historico', 'catalog_groups' inicializadas.")
    except Exception as e:
        print(f" [ERRO] Falha ao criar tabelas: {e}")
        return False

    # 3. Teste de Inserção (CRUD)
    print("\n[3/4] Testando inserção e isolamento de consulta...")
    test_cid = f"test_consulta_{uuid.uuid4().hex[:8]}"
    now_iso = datetime.now().isoformat()

    try:
        with get_db_session() as session:
            nova_consulta = Consulta(
                id=test_cid,
                nome="Consulta Teste Automatizado",
                grupo_id="grupo_teste_123",
                grupo_nome="Grupo de Testes Automatizados",
                comunidade_nome="Comunidade Teste",
                total_mensagens=1,
                criado_em=now_iso,
                atualizado_em=now_iso,
                status="Ativa",
            )
            session.add(nova_consulta)

            msg_teste = Message(
                id=f"msg_{uuid.uuid4().hex[:8]}",
                consulta_id=test_cid,
                grupo_id="grupo_teste_123",
                grupo_nome="Grupo de Testes Automatizados",
                remetente="Participante Teste",
                texto="Olá! Esta é uma mensagem de teste automatizado para validação do Neon/PostgreSQL.",
                data_hora="17/09/2026, 20:30",
                data_hora_ts=datetime.now().timestamp(),
                created_at=now_iso,
            )
            session.add(msg_teste)

            coleta_audit = ColetaHistorico(
                id=f"coleta_{uuid.uuid4().hex[:8]}",
                consulta_id=test_cid,
                grupo_id="grupo_teste_123",
                grupo_nome="Grupo de Testes Automatizados",
                executado_em=now_iso,
                total_extraido=1,
                total_acumulado=1,
                status="Sucesso",
            )
            session.add(coleta_audit)
        print(" [OK] Consulta, mensagem e registro de auditoria gravados com sucesso.")
    except Exception as e:
        print(f" [ERRO] Falha na inserção: {e}")
        return False

    # 4. Teste de Consulta e Limpeza
    print("\n[4/4] Consultando dados inseridos e efetuando limpeza...")
    try:
        with get_db_session() as session:
            c = session.query(Consulta).filter(Consulta.id == test_cid).first()
            assert c is not None, "Consulta não encontrada"
            assert len(c.messages) == 1, "Mensagem vinculada não encontrada"
            print(f" [OK] Leitura confirmada: Consulta '{c.nome}' possui {len(c.messages)} mensagem(ns).")

            # Remove os dados de teste
            session.delete(c)
        print(" [OK] Limpeza de dados de teste concluída.")
    except Exception as e:
        print(f" [ERRO] Falha na leitura/deleção: {e}")
        return False

    print("\n" + "=" * 60)
    print(" TODOS OS TESTES FORAM CONCLUÍDOS COM SUCESSO!")
    print("=" * 60)
    return True


if __name__ == "__main__":
    sucesso = run_tests()
    sys.exit(0 if sucesso else 1)
