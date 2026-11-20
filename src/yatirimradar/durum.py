"""Sistem durumu panosu: ne kadar veri var, kaçı haritada, kaç geocode bekliyor.

``durum`` CLI komutu bunu basar — "ne durumdayız / ne kadar kaldı" her sorulduğunda.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select

from yatirimradar.geocode.arkaplan import lokator_ozetten
from yatirimradar.store.db import session_scope
from yatirimradar.store.models import Fiyat, KatalizorOlay, Parsel


@dataclass
class Durum:
    """Hattın anlık durumu."""

    katalizor_toplam: int = 0
    tur_dagilimi: dict[str, int] = field(default_factory=dict)
    geom_var: int = 0
    geom_yok: int = 0
    geocode_bekleyen: int = 0  # geom yok AMA ozet'te ada/parsel var → çözülebilir
    fiyat_toplam: int = 0
    parsel_toplam: int = 0
    valid_min: datetime | None = None
    valid_max: datetime | None = None
    son_cekim: datetime | None = None

    @property
    def geocode_orani(self) -> float:
        return self.geom_var / self.katalizor_toplam if self.katalizor_toplam else 0.0


def durum_topla() -> Durum:
    """DB'yi sorgula, durum nesnesini doldur."""
    d = Durum()
    with session_scope() as s:
        for tur, sayi in s.execute(
            select(KatalizorOlay.tur, func.count()).group_by(KatalizorOlay.tur)
        ):
            d.tur_dagilimi[tur] = int(sayi)
        d.katalizor_toplam = sum(d.tur_dagilimi.values())
        d.geom_var = int(
            s.scalar(select(func.count()).where(KatalizorOlay.geom.isnot(None))) or 0
        )
        d.geom_yok = d.katalizor_toplam - d.geom_var
        d.fiyat_toplam = int(s.scalar(select(func.count()).select_from(Fiyat)) or 0)
        d.parsel_toplam = int(s.scalar(select(func.count()).select_from(Parsel)) or 0)
        vmin, vmax, kt = s.execute(
            select(
                func.min(KatalizorOlay.valid_time),
                func.max(KatalizorOlay.valid_time),
                func.max(KatalizorOlay.knowledge_time),
            )
        ).one()
        d.valid_min, d.valid_max, d.son_cekim = vmin, vmax, kt

        # Geocode bekleyen: geom'suz ÇED olaylarından ozet'inde çözülebilir lokatör olanlar.
        bekleyen = 0
        for ilce, ozet in s.execute(
            select(KatalizorOlay.ilce, KatalizorOlay.ozet).where(
                KatalizorOlay.tur == "ced", KatalizorOlay.geom.is_(None)
            )
        ):
            lok = lokator_ozetten(ozet, ilce)
            if lok is not None and lok.geocode_edilebilir:
                bekleyen += 1
        d.geocode_bekleyen = bekleyen
    return d
