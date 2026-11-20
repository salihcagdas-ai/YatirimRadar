"""PostGIS gerektirmeyen fiyat sorguları için izole veritabanı."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


@pytest.fixture
def fiyat_session(tmp_path: Path) -> Iterator[Session]:
    engine = create_engine(f"sqlite:///{tmp_path / 'fiyat.sqlite'}")
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(
                """CREATE TABLE fiyat (
                    id INTEGER PRIMARY KEY, parsel_id INTEGER, tip TEXT NOT NULL,
                    il TEXT, ilce TEXT, mahalle TEXT, cadde_sokak TEXT,
                    fiyat_tl REAL, m2 REAL, birim_fiyat_tl_m2 REAL, kaynak TEXT NOT NULL,
                    kesinlik REAL, geom BLOB, raw_ref TEXT, valid_time DATETIME,
                    knowledge_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )"""
            )
        with Session(engine) as session:
            yield session
    finally:
        engine.dispose()
