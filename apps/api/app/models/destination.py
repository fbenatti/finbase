"""
Destination Tables — target schemas that source data is mapped into.

Flow: SourceConnection → SourceDestinationMapping → DestinationTable
Classification and Business Rules can reference destination tables for unified processing.
"""
from sqlalchemy import (
    Column, Integer, String, DateTime, JSON, Boolean, ForeignKey, Text, func, Index,
)
from app.database import Base


class DestinationTable(Base):
    """Metadata for a target/destination table (e.g. BILLING_CLOUD)."""
    __tablename__ = "destination_table"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True)
    description = Column(String(500), nullable=True)
    physical_name = Column(String(100), nullable=False, unique=True)
    # grain + optional Iceberg partition extras (identity / bucket); see destination_slice.py
    partition_spec_json = Column(JSON, nullable=True)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class DestinationColumn(Base):
    """Columns that belong to a DestinationTable."""
    __tablename__ = "destination_column"

    id = Column(Integer, primary_key=True, autoincrement=True)
    destination_table_id = Column(
        Integer, ForeignKey("destination_table.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    column_name = Column(String(200), nullable=False)
    data_type = Column(String(50), nullable=False, default="TEXT")
    is_required = Column(Boolean, nullable=False, default=False)
    position = Column(Integer, nullable=False, default=0)
    default_value = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_dest_col_table_name", "destination_table_id", "column_name", unique=True),
    )


class SourceDestinationMapping(Base):
    """Maps a source column to a destination column for a given connection + destination table."""
    __tablename__ = "source_destination_mapping"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_connection_id = Column(
        Integer, ForeignKey("source_connection.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    destination_table_id = Column(
        Integer, ForeignKey("destination_table.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    name = Column(String(200), nullable=True)
    source_column = Column(String(200), nullable=False)
    destination_column = Column(String(200), nullable=False)
    transform_type = Column(String(50), nullable=False, default="RENAME")
    transform_config = Column(JSON, nullable=True)
    priority = Column(Integer, nullable=False, default=0)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index(
            "ix_src_dest_map_unique",
            "source_connection_id", "destination_table_id", "source_column", "destination_column",
            unique=True,
        ),
    )
