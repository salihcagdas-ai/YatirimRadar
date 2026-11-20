"""Resmî rayiç kayıtlarını mahalle ve takdir yılı düzeyinde haritaya hazırlar."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from sqlalchemy import select

from yatirimradar.config import PROJECT_ROOT
from yatirimradar.geocode.yer import _fold
from yatirimradar.store.db import session_scope
from yatirimradar.store.fiyat import guncel_fiyatlar

type Yer = tuple[str, str, str]
_MAHALLE_SONEKI = re.compile(r"\s+(mah\.?|mahallesi)$", re.IGNORECASE)


def _yer_anahtari(il: str, ilce: str, mahalle: str) -> Yer:
    return _fold(il), _fold(ilce), _fold(mahalle)


@lru_cache(maxsize=1)
def _mahalle_noktalari() -> tuple[datetime | None, dict[Yer, dict[str, Any]]]:
    """Kaynağı kayıtlı mahalle işaretleri. Harita açılırken dış servis çağrılmaz."""
    dosya = PROJECT_ROOT / "data" / "seed" / "mahalle_noktalari.json"
    try:
        veri = json.loads(dosya.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, {}
    noktalar = {_yer_anahtari(n["il"], n["ilce"], n["mahalle"]): n for n in veri["noktalar"]}
    return datetime.fromisoformat(veri["bilgi_zamani"]), noktalar


@dataclass
class _MahalleOzeti:
    il: str
    ilce: str
    mahalle: str
    adet: int = 0
    pozitif_adet: int = 0
    toplam: float = 0.0
    en_dusuk: float = float("inf")
    en_yuksek: float = 0.0
    kaynaklar: set[str] = field(default_factory=set)

    def ekle(self, fiyat: float, kaynak: str) -> None:
        self.adet += 1
        self.kaynaklar.add(kaynak)
        if fiyat > 0:
            self.pozitif_adet += 1
            self.toplam += fiyat
            self.en_dusuk = min(self.en_dusuk, fiyat)
            self.en_yuksek = max(self.en_yuksek, fiyat)

    def feature(self, yil: int, nokta: dict[str, Any] | None) -> dict[str, Any]:
        return {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": nokta["koordinat"]} if nokta else None,
            "properties": {
                "il": self.il,
                "ilce": self.ilce,
                "mahalle": self.mahalle,
                "yil": yil,
                "adet": self.adet,
                "pozitif_adet": self.pozitif_adet,
                "min_tl_m2": self.en_dusuk if self.pozitif_adet else None,
                "ort_tl_m2": round(self.toplam / self.pozitif_adet, 2)
                if self.pozitif_adet
                else None,
                "max_tl_m2": self.en_yuksek if self.pozitif_adet else None,
                "kaynaklar": sorted(self.kaynaklar),
                "konum_kaynagi": "OpenStreetMap neighborhood point" if nokta else None,
                "konum_url": f"https://www.openstreetmap.org/node/{nokta['osm_node']}"
                if nokta
                else None,
            },
        }


def rayic_ozet_geojson(yil: int | None = None, as_of: datetime | None = None) -> dict[str, Any]:
    """Her mahalle ve takdir yılı için bir Feature; konumu bilinmeyenler geometry=null taşır."""
    kesim = (as_of or datetime.now(UTC)).astimezone(UTC)
    fiyatlar = guncel_fiyatlar(kesim).c
    stmt = select(
        fiyatlar.il,
        fiyatlar.ilce,
        fiyatlar.mahalle,
        fiyatlar.valid_time,
        fiyatlar.birim_fiyat_tl_m2,
        fiyatlar.kaynak,
    ).where(
        fiyatlar.tip == "rayic",
        fiyatlar.il.is_not(None),
        fiyatlar.ilce.is_not(None),
        fiyatlar.mahalle.is_not(None),
        fiyatlar.valid_time.is_not(None),
    )
    if yil is not None:
        stmt = stmt.where(
            fiyatlar.valid_time.between(
                datetime(yil, 1, 1, tzinfo=UTC),
                datetime(yil, 12, 31, 23, 59, 59, 999999, tzinfo=UTC),
            )
        )
    gruplar: dict[tuple[Yer, int], _MahalleOzeti] = {}
    with session_scope() as session:
        for il, ilce, mahalle, tarih, fiyat, kaynak in session.execute(stmt):
            il, ilce = il.strip(), ilce.strip()
            mahalle = _MAHALLE_SONEKI.sub("", mahalle.strip())
            if not (il and ilce and mahalle):
                continue
            takdir_yili = tarih.astimezone(UTC).year if tarih.tzinfo else tarih.year
            anahtar = (_yer_anahtari(il, ilce, mahalle), takdir_yili)
            if anahtar not in gruplar:
                gruplar[anahtar] = _MahalleOzeti(il, ilce, mahalle)
            gruplar[anahtar].ekle(fiyat, kaynak)

    bilgi_zamani, noktalar = _mahalle_noktalari()
    return {
        "type": "FeatureCollection",
        "features": [
            grup.feature(y, noktalar.get(yer) if bilgi_zamani and kesim >= bilgi_zamani else None)
            for (yer, y), grup in sorted(gruplar.items())
        ],
    }
