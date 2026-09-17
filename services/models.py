from __future__ import annotations

from sqlalchemy import Column, Float, ForeignKey, Integer, String, Text, Index
from sqlalchemy.orm import relationship

from services.database import Base


class Consulta(Base):
    """
    Representa uma sessão/consulta de análise independente.
    No Neon/Postgres, substitui a criação de múltiplos arquivos .db no disco.
    """
    __tablename__ = "consultas"

    id = Column(String, primary_key=True)
    nome = Column(String, nullable=False)
    grupo_id = Column(String, nullable=True, index=True)
    grupo_nome = Column(String, nullable=True)
    comunidade_nome = Column(String, nullable=True)
    total_mensagens = Column(Integer, default=0)
    criado_em = Column(String, nullable=False)
    atualizado_em = Column(String, nullable=False)
    status = Column(String, default="Ativa")
    metadata_json = Column(Text, nullable=True)

    messages = relationship("Message", back_populates="consulta", cascade="all, delete-orphan")
    coletas = relationship("ColetaHistorico", back_populates="consulta", cascade="all, delete-orphan")


class Message(Base):
    """
    Representa uma mensagem extraída de grupo do WhatsApp.
    """
    __tablename__ = "messages"

    id = Column(String, primary_key=True)
    consulta_id = Column(String, ForeignKey("consultas.id", ondelete="CASCADE"), nullable=True, index=True)
    coleta_id = Column(String, nullable=True, index=True)
    grupo_id = Column(String, nullable=True, index=True)
    grupo_nome = Column(String, nullable=True)
    comunidade_nome = Column(String, nullable=True)
    coletado_em = Column(String, nullable=True)
    meses_back = Column(Integer, nullable=True)
    semanas_back = Column(Integer, nullable=True)
    data_hora = Column(String, nullable=True)
    data_hora_ts = Column(Float, nullable=True, index=True)
    remetente = Column(String, nullable=True, index=True)
    texto = Column(Text, nullable=True)
    texto_normalizado = Column(Text, nullable=True)
    is_reply = Column(Integer, default=0)
    reply_author = Column(String, nullable=True)
    reply_text = Column(Text, nullable=True)
    has_attachments = Column(Integer, default=0, index=True)
    attachments_json = Column(Text, nullable=True)
    reactions_json = Column(Text, nullable=True)
    topics_json = Column(Text, nullable=True)
    transcript = Column(Text, nullable=True)
    created_at = Column(String, nullable=True)

    consulta = relationship("Consulta", back_populates="messages")


class ColetaHistorico(Base):
    """
    Registro histórico e auditoria de cada ciclo de extração ou importação executado.
    """
    __tablename__ = "coletas_historico"

    id = Column(String, primary_key=True)
    consulta_id = Column(String, ForeignKey("consultas.id", ondelete="CASCADE"), nullable=True, index=True)
    grupo_id = Column(String, nullable=True, index=True)
    grupo_nome = Column(String, nullable=True)
    comunidade_nome = Column(String, nullable=True)
    executado_em = Column(String, nullable=True, index=True)
    unidade_tempo = Column(String, nullable=True)
    valor = Column(Integer, default=7)
    tipo_filtro = Column(String, nullable=True)
    total_extraido = Column(Integer, default=0)
    total_acumulado = Column(Integer, default=0)
    status = Column(String, default="Concluído")
    detalhes_json = Column(Text, nullable=True)

    consulta = relationship("Consulta", back_populates="coletas")


class CatalogGroup(Base):
    """
    Catálogo persistente de grupos descobertos no WhatsApp Web.
    """
    __tablename__ = "catalog_groups"

    id = Column(String, primary_key=True)
    nome = Column(String, nullable=False, index=True)
    comunidade = Column(String, nullable=True)
    atualizado_em = Column(String, nullable=False)
