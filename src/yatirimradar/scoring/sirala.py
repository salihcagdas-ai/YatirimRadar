"""Fırsat sıralaması: ``katalizor_olay`` × ``fiyat`` (rayiç) → skorlu liste.

Bitemporal as-of: yalnız ``knowledge_time <= as_of`` olaylar (backtest dürüstlüğü).
Rayiç, olayın bilinen mahallesinin veya ilçesinin en güncel takdir yılından alınır.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select

from yatirimradar.geocode.yer import _fold, parse_yer
from yatirimradar.scoring.skor import SkorBilesen, olcek_belirle, skorla
from yatirimradar.store.db import session_scope
from yatirimradar.store.fiyat import guncel_fiyatlar
from yatirimradar.store.models import KatalizorOlay
from yatirimradar.store.olay import guncel_olay_idleri


def _anahtar(il: str | None, ilce: str | None) -> tuple[str, str]:
    return (_fold(il or "").strip(), _fold(ilce or "").strip())


@dataclass(frozen=True)
class SiraliFirsat:
    """Skorlanmış tek fırsat (katalizör olayı + bölge rayici)."""

    olay_id: int
    tur: str
    il: str | None
    ilce: str | None
    ozet: str | None
    kesinlik: float | None
    valid_time: datetime | None
    geomlu: bool
    rayic_tl_m2: float | None
    bilesen: SkorBilesen
    fiyat_tipi: str | None = None
    fiyat_duzeyi: str | None = None
    fiyat_yili: int | None = None

    @property
    def skor(self) -> float:
        return self.bilesen.skor


@dataclass(frozen=True)
class BolgeFiyati:
    birim_fiyat: float
    tip: str
    duzey: str
    yil: int | None


type FiyatHaritasi = dict[tuple[str, str, str], BolgeFiyati]


def _fiyat_haritasi(il: str | None, as_of: datetime) -> FiyatHaritasi:
    """Her bölge için rayiç öncelikli, tek takdir yılından pozitif gözlem ortalaması."""
    fiyat = guncel_fiyatlar(as_of).c
    stmt = select(
        fiyat.il, fiyat.ilce, fiyat.mahalle, fiyat.tip, fiyat.valid_time, fiyat.birim_fiyat_tl_m2,
    ).where(fiyat.birim_fiyat_tl_m2 > 0)
    if il:
        stmt = stmt.where(func.lower(fiyat.il) == func.lower(il))
    gruplar: dict[tuple[str, str, str, str, int], list[float]] = {}
    with session_scope() as s:
        for f_il, ilce, mahalle, tip, tarih, birim in s.execute(stmt):
            if not (f_il and ilce):
                continue
            bolge = _anahtar(f_il, ilce)
            yil = tarih.year if tarih else 0
            mahalle_adi = _mahalle_anahtari(mahalle)
            for alan in (("", mahalle_adi) if mahalle_adi else ("",)):
                gruplar.setdefault((*bolge, alan, tip, yil), []).append(birim)

    degerler: FiyatHaritasi = {}
    for (f_il, ilce, mahalle, tip, yil), fiyatlar in gruplar.items():
        konum = (f_il, ilce, mahalle)
        onceki = degerler.get(konum)
        if onceki and (onceki.tip == "rayic", onceki.yil or 0) >= (tip == "rayic", yil):
            continue
        degerler[konum] = BolgeFiyati(
            sum(fiyatlar) / len(fiyatlar), tip, "mahalle" if mahalle else "ilce", yil or None,
        )
    return degerler


def _mahalle_anahtari(ad: str | None) -> str:
    return re.sub(r"\s+(?:mah|mh|mahalle|mahallesi)\.?$", "", _fold(ad or "").strip())


def _olay_fiyati(
    fiyatlar: FiyatHaritasi, bolge: tuple[str, str], tur: str, ozet: str | None,
) -> BolgeFiyati | None:
    ilce_fiyati = fiyatlar.get((*bolge, ""))
    if not ilce_fiyati or tur != "ced" or not ozet:
        return ilce_fiyati
    # Sadece ÇED'in yer alanına bak. Birden çok mahalleyi kapsayan projelerde
    # bir mahalleyi temsili kabul etmek skoru yanıltır.
    yer = _fold(ozet.partition(" @ ")[2].partition(" [")[0])
    yer = re.sub(r"\b(?:mh|mah)\b\.?", "mahallesi", yer)
    mahalleler = re.findall(r"\bmahalle(?:si|leri)\b", yer)
    on_ek = yer.partition("mahallesi")[0]
    if mahalleler != ["mahallesi"] or re.search(r"\b(?:ve|ilceleri)\b|/", on_ek):
        return ilce_fiyati
    lok = parse_yer(yer)
    if (lok.il and lok.il != bolge[0]) or (lok.ilce and lok.ilce != bolge[1]):
        return ilce_fiyati
    mahalle_fiyati = fiyatlar.get((*bolge, _mahalle_anahtari(lok.mahalle)))
    if mahalle_fiyati and (mahalle_fiyati.tip == "rayic" or ilce_fiyati.tip == "ilan"):
        return mahalle_fiyati
    return ilce_fiyati


def _ilce_yogunluk_map(
    il: str | None = None, as_of: datetime | None = None
) -> dict[tuple[str, str], int]:
    """(il, ilçe) → o ilçedeki katalizör olay sayısı (yoğunluk/sıcaklık sinyali)."""
    as_of = as_of or datetime.now(UTC)
    stmt = (
        select(KatalizorOlay.il, KatalizorOlay.ilce, func.count())
        .where(
            KatalizorOlay.knowledge_time <= as_of,
            KatalizorOlay.id.in_(guncel_olay_idleri(as_of)),
        )
        .group_by(KatalizorOlay.il, KatalizorOlay.ilce)
    )
    if il:
        stmt = stmt.where(KatalizorOlay.il == il)
    sayilar: dict[tuple[str, str], int] = {}
    with session_scope() as s:
        for o_il, ilce, n in s.execute(stmt):
            anahtar = _anahtar(o_il, ilce)
            sayilar[anahtar] = sayilar.get(anahtar, 0) + int(n)
    return sayilar


def firsatlari_sirala(
    il: str | None = None,
    as_of: datetime | None = None,
    limit: int | None = 20,
    sadece_geomlu: bool = False,
) -> list[SiraliFirsat]:
    """Katalizör olaylarını rayiç + ilçe yoğunluğu bağlamında skorla, azalan sırala (top-N)."""
    as_of = as_of or datetime.now(UTC)
    fiyatlar = _fiyat_haritasi(il, as_of)
    yogunluk = _ilce_yogunluk_map(il, as_of)

    stmt = select(
        KatalizorOlay.id,
        KatalizorOlay.tur,
        KatalizorOlay.il,
        KatalizorOlay.ilce,
        KatalizorOlay.ozet,
        KatalizorOlay.kesinlik,
        KatalizorOlay.valid_time,
        KatalizorOlay.geom.isnot(None),
        func.ST_Area(func.geography(KatalizorOlay.geom)),
    ).where(
        KatalizorOlay.knowledge_time <= as_of,
        KatalizorOlay.id.in_(guncel_olay_idleri(as_of)),  # düzeltme zinciri: son satır
    )
    if il:
        stmt = stmt.where(KatalizorOlay.il == il)
    if sadece_geomlu:
        stmt = stmt.where(KatalizorOlay.geom.isnot(None))

    firsatlar: list[SiraliFirsat] = []
    with session_scope() as s:
        for oid, tur, o_il, ilce, ozet, kesinlik, vt, geomlu, alan_m2 in s.execute(stmt):
            anahtar = _anahtar(o_il, ilce)
            fiyat = _olay_fiyati(fiyatlar, anahtar, tur, ozet)
            r = fiyat.birim_fiyat if fiyat else None
            bilesen = skorla(
                tur, kesinlik, vt, r, simdi=as_of,
                ilce_katalizor_sayisi=yogunluk.get(anahtar, 1),
                buyukluk_olcek=olcek_belirle(ozet, tur, alan_m2),
            )
            firsatlar.append(
                SiraliFirsat(
                    olay_id=int(oid),
                    tur=tur,
                    il=o_il,
                    ilce=ilce,
                    ozet=ozet,
                    kesinlik=kesinlik,
                    valid_time=vt,
                    geomlu=bool(geomlu),
                    rayic_tl_m2=r,
                    bilesen=bilesen,
                    fiyat_tipi=fiyat.tip if fiyat else None,
                    fiyat_duzeyi=fiyat.duzey if fiyat else None,
                    fiyat_yili=fiyat.yil if fiyat else None,
                )
            )
    firsatlar.sort(key=lambda f: f.skor, reverse=True)
    return firsatlar[:limit]


def firsatlar_geojson(
    il: str | None = None,
    as_of: datetime | None = None,
    limit: int = 300,
) -> dict[str, Any]:
    """Geometrisi çözülmüş skorlu fırsatları **GeoJSON FeatureCollection** olarak döndür.

    Harita (MapLibre) ve API için: her özellik geom + skor + açıklanabilir bileşenler taşır.
    """
    as_of = as_of or datetime.now(UTC)
    fiyatlar = _fiyat_haritasi(il, as_of)
    yogunluk = _ilce_yogunluk_map(il, as_of)
    stmt = select(
        KatalizorOlay.id,
        KatalizorOlay.tur,
        KatalizorOlay.il,
        KatalizorOlay.ilce,
        KatalizorOlay.ozet,
        KatalizorOlay.kesinlik,
        KatalizorOlay.valid_time,
        func.ST_AsGeoJSON(KatalizorOlay.geom),
        func.ST_Area(func.geography(KatalizorOlay.geom)),
    ).where(
        KatalizorOlay.knowledge_time <= as_of,
        KatalizorOlay.geom.isnot(None),
        KatalizorOlay.id.in_(guncel_olay_idleri(as_of)),  # düzeltme zinciri: son satır
    )
    if il:
        stmt = stmt.where(KatalizorOlay.il == il)

    feats: list[dict[str, Any]] = []
    with session_scope() as s:
        for oid, tur, o_il, ilce, ozet, kesinlik, vt, geojson_str, alan_m2 in s.execute(stmt):
            if not geojson_str:
                continue
            anahtar = _anahtar(o_il, ilce)
            fiyat = _olay_fiyati(fiyatlar, anahtar, tur, ozet)
            r = fiyat.birim_fiyat if fiyat else None
            b = skorla(
                tur, kesinlik, vt, r, simdi=as_of,
                ilce_katalizor_sayisi=yogunluk.get(anahtar, 1),
                buyukluk_olcek=olcek_belirle(ozet, tur, alan_m2),
            )
            feats.append(
                {
                    "type": "Feature",
                    "geometry": json.loads(geojson_str),
                    "properties": {
                        "id": int(oid),
                        "tur": tur,
                        "il": o_il,
                        "ilce": ilce,
                        "ozet": ozet,
                        "skor": round(b.skor, 3),
                        "rayic_tl_m2": r,
                        "fiyat_tipi": fiyat.tip if fiyat else None,
                        "fiyat_duzeyi": fiyat.duzey if fiyat else None,
                        "fiyat_yili": fiyat.yil if fiyat else None,
                        "tur_agirlik": round(b.tur_agirlik, 2),
                        "kesinlik": round(b.kesinlik, 2),
                        "tazelik": round(b.tazelik, 2),
                        "ucuzluk": round(b.ucuzluk, 2),
                        "yogunluk": round(b.yogunluk, 2),
                        "buyukluk": round(b.buyukluk, 2),
                        "valid_time": vt.date().isoformat() if vt else None,
                    },
                }
            )
    feats.sort(key=lambda f: float(f["properties"]["skor"]), reverse=True)
    return {"type": "FeatureCollection", "features": feats[:limit]}
