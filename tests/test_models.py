"""Stage B: bitemporal şema metadata testleri (DB gerektirmez)."""

from __future__ import annotations

from yatirimradar.store.models import Base, Fiyat


def test_all_tables_present() -> None:
    names = set(Base.metadata.tables)
    assert {"parsel", "parsel_soyagaci", "katalizor_olay", "fiyat"} <= names


def test_every_table_is_bitemporal() -> None:
    for table in Base.metadata.tables.values():
        assert "valid_time" in table.columns, f"{table.name} valid_time eksik"
        assert "knowledge_time" in table.columns, f"{table.name} knowledge_time eksik"
        # knowledge_time append-only filtre anahtarı → NOT NULL olmalı.
        assert table.columns["knowledge_time"].nullable is False


def test_fiyat_has_fk_to_parsel() -> None:
    assert any(fk.column.table.name == "parsel" for fk in Fiyat.__table__.foreign_keys)
