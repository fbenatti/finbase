from sqlalchemy import (
    Column, Integer, String, DateTime, Date, ForeignKey, func, Index, UniqueConstraint, Float,
)
from sqlalchemy.orm import relationship
from app.database import Base

IDEA_TYPES = ("FEATURE", "PAINEL", "BUG", "BUG_CRITICO", "MELHORIA")
IDEA_IMPACTS = ("BAIXO", "MEDIO", "ALTO", "CRITICO")
IDEA_DIFFICULTIES = ("XS", "S", "M", "L", "XL")
IDEA_STATUSES = (
    "NOVA", "EM_VOTACAO", "EM_ANALISE", "BACKLOG", "EM_IMPLEMENTACAO",
    "CONCLUIDA_ANALISE", "EM_PRODUCAO", "CONCLUIDA", "CANCELADA", "FECHADA",
)
VOTABLE_TYPES = ("FEATURE", "PAINEL")
TERMINAL_STATUSES = ("CANCELADA", "FECHADA", "CONCLUIDA")
IMPACT_WEIGHT = {"CRITICO": 4, "ALTO": 3, "MEDIO": 2, "BAIXO": 1}


class Idea(Base):
    __tablename__ = "idea"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(300), nullable=False)
    description = Column(String(8000))
    idea_type = Column(String(30), nullable=False, index=True)
    impact = Column(String(20), nullable=False, default="MEDIO")
    difficulty = Column(String(10), nullable=False, default="M")
    due_date = Column(Date)
    status = Column(String(30), nullable=False, default="NOVA", index=True)
    author_id = Column(Integer, ForeignKey("user_account.id"), nullable=False, index=True)
    votes_count = Column(Integer, nullable=False, default=0)
    priority_score = Column(Float, nullable=False, default=0)
    status_entered_at = Column(DateTime(timezone=True), server_default=func.now())
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    author = relationship("User", foreign_keys=[author_id])
    assignees = relationship("IdeaAssignee", back_populates="idea", cascade="all, delete-orphan")
    votes = relationship("IdeaVote", back_populates="idea", cascade="all, delete-orphan")
    comments = relationship("IdeaComment", back_populates="idea", cascade="all, delete-orphan")
    intervals = relationship("IdeaStatusInterval", back_populates="idea", cascade="all, delete-orphan")


class IdeaAssignee(Base):
    __tablename__ = "idea_assignee"
    __table_args__ = (UniqueConstraint("idea_id", "user_id", name="uq_idea_assignee"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    idea_id = Column(Integer, ForeignKey("idea.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("user_account.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    idea = relationship("Idea", back_populates="assignees")
    user = relationship("User")


class IdeaVote(Base):
    __tablename__ = "idea_vote"
    __table_args__ = (UniqueConstraint("idea_id", "user_id", name="uq_idea_vote"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    idea_id = Column(Integer, ForeignKey("idea.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("user_account.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    idea = relationship("Idea", back_populates="votes")
    user = relationship("User")


class IdeaComment(Base):
    __tablename__ = "idea_comment"

    id = Column(Integer, primary_key=True, autoincrement=True)
    idea_id = Column(Integer, ForeignKey("idea.id", ondelete="CASCADE"), nullable=False, index=True)
    author_id = Column(Integer, ForeignKey("user_account.id"), nullable=False)
    body = Column(String(4000), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    idea = relationship("Idea", back_populates="comments")
    author = relationship("User")


class IdeaStatusInterval(Base):
    __tablename__ = "idea_status_interval"
    __table_args__ = (Index("ix_idea_status_interval_idea_entered", "idea_id", "entered_at"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    idea_id = Column(Integer, ForeignKey("idea.id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(String(30), nullable=False, index=True)
    from_status = Column(String(30))
    entered_at = Column(DateTime(timezone=True), nullable=False)
    exited_at = Column(DateTime(timezone=True))
    duration_seconds = Column(Integer)
    actor_user_id = Column(Integer, ForeignKey("user_account.id"))

    idea = relationship("Idea", back_populates="intervals")
