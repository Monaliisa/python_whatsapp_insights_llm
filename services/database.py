from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker, Session

from services.paths import get_base_dir, get_data_dir, get_db_path

# Carrega variáveis do arquivo .env localizado na raiz do projeto
env_path = get_base_dir() / ".env"
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
else:
    load_dotenv()

Base = declarative_base()


def get_database_url() -> str:
    """
    Retorna a URL de conexão com o banco de dados.
    - Se a variável DATABASE_URL estiver configurada (ex: Neon/PostgreSQL), normaliza o protocolo para SQLAlchemy.
    - Caso contrário, utiliza o banco SQLite local em data/messages.db ou a consulta ativa.
    """
    raw_url = os.environ.get("DATABASE_URL", "").strip()

    if raw_url:
        # Corrige prefixos legados postgres:// para postgresql+psycopg:// ou postgresql://
        if raw_url.startswith("postgres://"):
            raw_url = raw_url.replace("postgres://", "postgresql+psycopg://", 1)
        elif raw_url.startswith("postgresql://") and not raw_url.startswith("postgresql+"):
            raw_url = raw_url.replace("postgresql://", "postgresql+psycopg://", 1)
        return raw_url

    # Fallback Local-First: SQLite
    sqlite_path = get_db_path()
    return f"sqlite:///{Path(sqlite_path).as_posix()}"


def is_postgres() -> bool:
    """Verifica se o banco ativo configurado é PostgreSQL / Neon."""
    url = get_database_url().lower()
    return url.startswith("postgresql") or url.startswith("postgres")


def create_db_engine(custom_url: str | None = None):
    """
    Cria uma engine SQLAlchemy otimizada para o tipo de banco (Neon Serverless ou SQLite).
    """
    url = custom_url or get_database_url()
    
    if "postgresql" in url or "postgres" in url:
        # Configuração de Pool otimizada para PostgreSQL Serverless (Neon)
        return create_engine(
            url,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,       # Verifica se a conexão está viva antes de usar (essencial para serverless)
            pool_recycle=300,        # Recicla conexões a cada 5 minutos evitando conexões zumbis
            future=True,
        )
    else:
        # Configuração para SQLite local
        return create_engine(
            url,
            connect_args={"check_same_thread": False},
            future=True,
        )


# Instância global da engine e SessionLocal
engine = create_db_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def reload_engine(custom_url: str | None = None) -> None:
    """Recarrega a engine caso a URL de conexão mude dinamicamente."""
    global engine, SessionLocal
    engine = create_db_engine(custom_url)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_database_tables(target_engine=None) -> None:
    """
    Garante que todas as tabelas declaradas nos modelos existam no banco de dados
    e aplica migrações de colunas faltantes de forma não-destrutiva.
    """
    eng = target_engine or engine
    import services.models  # Garante que os modelos sejam importados e registrados no Base
    Base.metadata.create_all(bind=eng)

    # Migrações automáticas de colunas adicionadas recentemente
    try:
        with eng.connect() as conn:
            # Verifica colunas em messages
            if "postgresql" in str(eng.url) or "postgres" in str(eng.url):
                res = conn.execute(text(
                    "SELECT column_name FROM information_schema.columns WHERE table_name = 'messages'"
                )).fetchall()
                cols_msg = [r[0].lower() for r in res]
                if cols_msg and "consulta_id" not in cols_msg:
                    conn.execute(text("ALTER TABLE messages ADD COLUMN consulta_id VARCHAR"))
                    conn.commit()

                res_c = conn.execute(text(
                    "SELECT column_name FROM information_schema.columns WHERE table_name = 'coletas_historico'"
                )).fetchall()
                cols_col = [r[0].lower() for r in res_c]
                if cols_col and "consulta_id" not in cols_col:
                    conn.execute(text("ALTER TABLE coletas_historico ADD COLUMN consulta_id VARCHAR"))
                    conn.commit()
            else:
                # SQLite
                res = conn.execute(text("PRAGMA table_info(messages)")).fetchall()
                cols_msg = [r[1].lower() for r in res]
                if cols_msg and "consulta_id" not in cols_msg:
                    conn.execute(text("ALTER TABLE messages ADD COLUMN consulta_id TEXT"))

                res_c = conn.execute(text("PRAGMA table_info(coletas_historico)")).fetchall()
                cols_col = [r[1].lower() for r in res_c]
                if cols_col and "consulta_id" not in cols_col:
                    conn.execute(text("ALTER TABLE coletas_historico ADD COLUMN consulta_id TEXT"))
    except Exception as e:
        print(f"[AVISO] Não foi possível verificar/migrar colunas automaticamente: {e}")


@contextmanager
def get_db_session() -> Generator[Session, None, None]:
    """
    Context manager seguro para gerenciar sessões do SQLAlchemy com commit e rollback automáticos.
    """
    session: Session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def test_db_connection() -> dict:
    """
    Testa a conectividade com o banco de dados e retorna informações do status.
    """
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT 1")).scalar()
            db_type = "PostgreSQL (Neon)" if is_postgres() else "SQLite (Local)"
            return {
                "success": True,
                "message": f"Conexão bem-sucedida com {db_type}",
                "db_type": db_type,
                "ping_result": result,
            }
    except Exception as e:
        return {
            "success": False,
            "message": f"Falha na conexão com o banco de dados: {e}",
            "db_type": "PostgreSQL (Neon)" if is_postgres() else "SQLite (Local)",
            "error": str(e),
        }
