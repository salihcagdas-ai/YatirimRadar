"""Bitemporal veri modeli (KRİTİK — kickoff §4, 08-T1/T2).

Her olay/fiyat/parsel kaydı iki zaman taşır:
- ``valid_time``     : gerçekte ne zaman geçerli (olay/takdir tarihi).
- ``knowledge_time`` : biz ne zaman öğrendik/çektik (transaction time).

Depo **append-only**: düzeltme = yeni knowledge_time'lı yeni satır; eski satır silinmez.
Tüm sorgular/backtest ``knowledge_time <= as_of`` filtresiyle çalışmalı.
Parsel **soyağacı** (ifraz/tevhit) ayrı tabloda izlenir.
KVKK: malik/PII saklanmaz — yalnız geometri + agregasyon.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import DateTime, Float, ForeignKey, Index, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SRID = 4326  # WGS84 (TKGM GeoJSON lon/lat)


class Base(DeclarativeBase):
    """Tüm modellerin ortak temel sınıfı (Alembic target_metadata)."""


class Bitemporal:
    """valid_time + knowledge_time taşıyan mixin (append-only)."""

    valid_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True, doc="Gerçekte ne zaman geçerli."
    )
    knowledge_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
        doc="Biz ne zaman öğrendik/çektik (backtest as-of filtresi).",
    )


class Parsel(Base, Bitemporal):
    """TKGM parsel anlık görüntüsü (append-only; ada/parsel + knowledge_time)."""

    __tablename__ = "parsel"

    id: Mapped[int] = mapped_column(primary_key=True)
    il: Mapped[str | None] = mapped_column(String(64))
    ilce: Mapped[str | None] = mapped_column(String(64))
    mahalle: Mapped[str | None] = mapped_column(String(128))
    mahalle_id: Mapped[str | None] = mapped_column(String(32), index=True)
    ada: Mapped[str | None] = mapped_column(String(32))
    parsel_no: Mapped[str | None] = mapped_column(String(32))
    alan_m2: Mapped[float | None] = mapped_column(Float)
    geom: Mapped[Any | None] = mapped_column(
        Geometry(geometry_type="MULTIPOLYGON", srid=SRID, spatial_index=True)
    )
    kaynak: Mapped[str] = mapped_column(String(32), default="tkgm")
    raw_ref: Mapped[str | None] = mapped_column(String(512), doc="data/raw ham yanıt yolu.")

    __table_args__ = (
        Index("ix_parsel_locator", "il", "ilce", "mahalle_id", "ada", "parsel_no"),
    )


class ParselSoyagaci(Base, Bitemporal):
    """İfraz/tevhit: eski ada-parsel ↔ yeni ada-parsel eşlemesi (08-T2)."""

    __tablename__ = "parsel_soyagaci"

    id: Mapped[int] = mapped_column(primary_key=True)
    il: Mapped[str | None] = mapped_column(String(64))
    ilce: Mapped[str | None] = mapped_column(String(64))
    mahalle_id: Mapped[str | None] = mapped_column(String(32), index=True)
    eski_ada: Mapped[str | None] = mapped_column(String(32))
    eski_parsel: Mapped[str | None] = mapped_column(String(32))
    yeni_ada: Mapped[str | None] = mapped_column(String(32))
    yeni_parsel: Mapped[str | None] = mapped_column(String(32))
    olay_turu: Mapped[str] = mapped_column(String(16), doc="ifraz | tevhit | tashih")


class KatalizorOlay(Base, Bitemporal):
    """Katalizör olayı: nokta/poligon + zaman + tür + aşama + kesinlik.

    Haber telifi: tam metin saklanmaz, yalnız özet + kaynak meta (05).
    """

    __tablename__ = "katalizor_olay"

    id: Mapped[int] = mapped_column(primary_key=True)
    tur: Mapped[str] = mapped_column(
        String(32), index=True, doc="kamulastirma | ced | kgm_yol | osb | haber | ..."
    )
    kaynak: Mapped[str] = mapped_column(String(64), doc="resmigazete | ced.csb | AA | ...")
    ozet: Mapped[str | None] = mapped_column(String(2000), doc="Özet (tam metin değil).")
    il: Mapped[str | None] = mapped_column(String(64))
    ilce: Mapped[str | None] = mapped_column(String(64))
    asama: Mapped[str | None] = mapped_column(
        String(16), doc="soylenti | planlandi | karar | basladi | tamamlandi"
    )
    kesinlik: Mapped[float | None] = mapped_column(Float, doc="Güven skoru 0..1.")
    geom: Mapped[Any | None] = mapped_column(
        Geometry(geometry_type="GEOMETRY", srid=SRID, spatial_index=True)
    )
    kaynak_url: Mapped[str | None] = mapped_column(String(1024))
    raw_ref: Mapped[str | None] = mapped_column(String(512))
    dedup_anahtar: Mapped[str | None] = mapped_column(
        String(512),
        index=True,
        doc="Kaynak-bağımsız doğal anahtar (idempotent yazım + düzeltme zinciri). "
        "ÇED: 'ced:{kayit_anahtari}' (URL değişse de sabit); diğerleri: kaynak_url.",
    )


class Fiyat(Base, Bitemporal):
    """Fiyat/değer kaydı: rayiç (resmi taban) veya ilan (asking proxy)."""

    __tablename__ = "fiyat"

    id: Mapped[int] = mapped_column(primary_key=True)
    parsel_id: Mapped[int | None] = mapped_column(ForeignKey("parsel.id"), index=True)
    tip: Mapped[str] = mapped_column(String(16), index=True, doc="rayic | ilan")
    il: Mapped[str | None] = mapped_column(String(64))
    ilce: Mapped[str | None] = mapped_column(String(64))
    mahalle: Mapped[str | None] = mapped_column(String(128))
    cadde_sokak: Mapped[str | None] = mapped_column(String(256))
    fiyat_tl: Mapped[float | None] = mapped_column(Float)
    m2: Mapped[float | None] = mapped_column(Float)
    birim_fiyat_tl_m2: Mapped[float | None] = mapped_column(Float)
    kaynak: Mapped[str] = mapped_column(String(64))
    kesinlik: Mapped[float | None] = mapped_column(Float)
    geom: Mapped[Any | None] = mapped_column(
        Geometry(geometry_type="POINT", srid=SRID, spatial_index=True)
    )
    raw_ref: Mapped[str | None] = mapped_column(String(512))
