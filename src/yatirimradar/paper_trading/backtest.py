"""Bitemporal backtest / kâğıt-üstü izleme iskeleti.

İki eksen:
- **knowledge_time (as-of):** "T anında *bildiğimiz* kadarıyla hangi fırsatlar açıktı?"
  Gerçek bitemporal backtest budur (``knowledge_time <= T``). Veri zamanla biriktikçe
  anlamlanır; toplu yükleme yapıldıysa knowledge_time tek-tip olur (dejenere).
- **valid_time (olay takvimi):** katalizör kararlarının *gerçekleştiği* tarihe göre
  birikim — mevcut veriyle anlamlı (kararlar 2024→2026'ya yayılır).

Getiri ölçümü (pozisyon → ileri fiyat değişimi) **fiyat-zaman serisi** gelince eklenir;
şimdilik açık pozisyon kümesi ve karar yılı takvimi üretilir.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from yatirimradar.scoring.sirala import SiraliFirsat, firsatlari_sirala
from yatirimradar.store.db import session_scope
from yatirimradar.store.models import KatalizorOlay


@dataclass(frozen=True)
class TakvimSatiri:
    """Bir yıldaki yeni + kümülatif açık pozisyon sayısı ve ortalama skor."""

    yil: int
    yeni: int
    kumulatif: int
    ort_skor: float


def takvim_topla(kayitlar: list[tuple[int | None, float]]) -> list[TakvimSatiri]:
    """``(yil, skor)`` listesini yıla göre kümülatif takvime topla (saf — DB yok)."""
    yil_say: dict[int, list[float]] = {}
    for yil, skor in kayitlar:
        if yil is None:
            continue
        yil_say.setdefault(yil, []).append(skor)
    satirlar: list[TakvimSatiri] = []
    kumulatif = 0
    for yil in sorted(yil_say):
        skorlar = yil_say[yil]
        kumulatif += len(skorlar)
        satirlar.append(
            TakvimSatiri(
                yil=yil,
                yeni=len(skorlar),
                kumulatif=kumulatif,
                ort_skor=round(sum(skorlar) / len(skorlar), 3),
            )
        )
    return satirlar


def acik_pozisyonlar(
    as_of: datetime | None = None,
    il: str | None = None,
    esik: float = 0.4,
    limit: int | None = None,
) -> list[SiraliFirsat]:
    """``as_of`` (knowledge_time) itibarıyla skoru ``esik`` üstü fırsatlar = açık pozisyonlar."""
    return [f for f in firsatlari_sirala(il=il, as_of=as_of, limit=limit) if f.skor >= esik]


def pozisyon_takvimi(pozisyonlar: list[SiraliFirsat]) -> list[TakvimSatiri]:
    """Aynı as-of kesitindeki pozisyonları gerçek karar yılına göre grupla."""
    kayitlar = [
        (f.valid_time.year if f.valid_time else None, f.skor) for f in pozisyonlar
    ]
    return takvim_topla(kayitlar)


def olay_takvimi(
    il: str | None = None, esik: float = 0.4, as_of: datetime | None = None
) -> list[TakvimSatiri]:
    """Belirtilen tarihte bilinen açık pozisyonların karar yılı takvimi."""
    return pozisyon_takvimi(acik_pozisyonlar(as_of=as_of, il=il, esik=esik))


def knowledge_zaman_araligi() -> tuple[datetime | None, datetime | None]:
    """DB'deki knowledge_time min/max — bitemporal backtest'in anlamlı olup olmadığını gösterir."""
    from sqlalchemy import func, select

    with session_scope() as s:
        row = s.execute(
            select(func.min(KatalizorOlay.knowledge_time), func.max(KatalizorOlay.knowledge_time))
        ).one()
    return row[0], row[1]
