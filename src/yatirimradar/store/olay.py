"""Harita ve sıralamada aynı bilgi tarihindeki olay sürümlerinin seçimi."""

from datetime import datetime

from sqlalchemy import Select, case, func, select

from yatirimradar.store.models import KatalizorOlay


def guncel_olay_idleri(as_of: datetime) -> Select[tuple[int]]:
    """Her doğal anahtarın son bilinen sürümü. Anahtarsız eski satırlar ayrı olaylardır."""
    anahtar = KatalizorOlay.dedup_anahtar
    sira = func.row_number().over(
        partition_by=(anahtar, case((anahtar.is_(None), KatalizorOlay.id))),
        order_by=(KatalizorOlay.knowledge_time.desc(), KatalizorOlay.id.desc()),
    )
    surumler = (
        select(KatalizorOlay.id, sira.label("sira"))
        .where(KatalizorOlay.knowledge_time <= as_of)
        .subquery()
    )
    return select(surumler.c.id).where(surumler.c.sira == 1)
