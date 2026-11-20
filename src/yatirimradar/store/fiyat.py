"""Skor ve harita için aynı tarih itibarıyla bilinen gerçek fiyat gözlemleri."""

from datetime import datetime

from sqlalchemy import Subquery, func, or_, select

from yatirimradar.store.models import Fiyat


def guncel_fiyatlar(as_of: datetime) -> Subquery:
    """Her adres, fiyat tipi ve takdir tarihi için o an bilinen son düzeltme."""
    sira = func.row_number().over(
        partition_by=(
            Fiyat.tip, Fiyat.il, Fiyat.ilce, Fiyat.mahalle, Fiyat.cadde_sokak, Fiyat.valid_time,
        ),
        order_by=(Fiyat.knowledge_time.desc(), Fiyat.id.desc()),
    )
    surumler = (
        select(
            Fiyat.id, Fiyat.tip, Fiyat.il, Fiyat.ilce, Fiyat.mahalle, Fiyat.cadde_sokak,
            Fiyat.valid_time, Fiyat.knowledge_time, Fiyat.birim_fiyat_tl_m2, Fiyat.kaynak,
            Fiyat.parsel_id, sira.label("sira"),
        )
        .where(
            Fiyat.tip.in_(("rayic", "ilan")),
            Fiyat.knowledge_time <= as_of,
            or_(Fiyat.valid_time <= as_of, Fiyat.valid_time.is_(None)),
            func.lower(func.trim(Fiyat.kaynak)) != "placeholder",
        )
        .subquery()
    )
    return (
        select(surumler)
        .where(
            surumler.c.sira == 1,
            surumler.c.birim_fiyat_tl_m2 >= 0,
            surumler.c.birim_fiyat_tl_m2 < float("inf"),
        )
        .subquery()
    )
