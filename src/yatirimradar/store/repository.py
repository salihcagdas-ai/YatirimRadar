"""Bitemporal yazma yardımcıları (append-only).

TKGM sonucu → ``parsel`` + ``parsel_soyagaci`` satırları. Geometri GeoJSON →
shapely → MultiPolygon → GeoAlchemy2 (SRID 4326). Yalnız geometri+alan+lokatör
saklanır (KVKK; malik/PII zaten gelmiyor).
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any

from geoalchemy2.elements import WKBElement
from geoalchemy2.shape import from_shape
from shapely.geometry import MultiPolygon, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from sqlalchemy import func, select

from yatirimradar.ingestion.tkgm import ParselSonuc, parse_tr_number
from yatirimradar.store.db import session_scope
from yatirimradar.store.models import Fiyat, KatalizorOlay, Parsel, ParselSoyagaci

_DATE_RE = re.compile(r"(\d{2})/(\d{2})/(\d{4})")


def _metin(s: str | None, n: int) -> str | None:
    """Kontrol karakterlerini (NUL dahil) temizle + ``n``'e kırp (Postgres reddetmesin)."""
    if s is None:
        return None
    temiz = "".join(c if (c >= " " or c in "\t\n") else " " for c in s).strip()
    return temiz[:n] if temiz else None


def geojson_to_multipolygon(geometry: dict[str, object] | None) -> WKBElement | None:
    """GeoJSON geometri → MultiPolygon WKBElement (SRID 4326). Polygon ise sarmalar."""
    if not geometry:
        return None
    shp: BaseGeometry = shape(geometry)
    if shp.geom_type == "Polygon":
        shp = MultiPolygon([shp])
    elif shp.geom_type != "MultiPolygon":
        return None
    return from_shape(shp, srid=4326)


def geojsonlar_to_multipolygon(geometriler: list[dict[str, object] | None]) -> WKBElement | None:
    """Birden çok GeoJSON poligonunu tek MultiPolygon'a birleştir (SRID 4326)."""
    polys: list[BaseGeometry] = []
    for g in geometriler:
        if not g:
            continue
        shp = shape(g)
        if shp.geom_type == "Polygon":
            polys.append(shp)
        elif shp.geom_type == "MultiPolygon":
            polys.extend(shp.geoms)
    if not polys:
        return None
    merged = unary_union(polys)
    if merged.geom_type == "Polygon":
        merged = MultiPolygon([merged])
    elif merged.geom_type != "MultiPolygon":
        return None
    return from_shape(merged, srid=4326)


def guncelle_katalizor_geom(
    olay_id: int,
    geom: WKBElement | None,
    kesinlik: float | None = None,
    il: str | None = None,
    ilce: str | None = None,
) -> bool:
    """Mevcut katalizör olayına türetilmiş geometri (+ kesinlik, otoriter il/ilçe) ekle."""
    if geom is None:
        return False
    with session_scope() as s:
        row = s.get(KatalizorOlay, olay_id)
        if row is None:
            return False
        row.geom = geom
        if kesinlik is not None:
            row.kesinlik = kesinlik
        if il:
            row.il = _metin(il, 64)
        if ilce:
            row.ilce = _metin(ilce, 64)
        return True


def kamulastirma_olaylari(karar_no: str) -> list[int]:
    """``Karar Sayısı: {karar_no}`` içeren kamulaştırma olaylarının id'leri."""
    with session_scope() as s:
        rows = s.execute(
            select(KatalizorOlay.id).where(
                KatalizorOlay.tur == "kamulastirma",
                KatalizorOlay.ozet.like(f"%Karar Sayısı: {karar_no}%"),
            )
        ).all()
        return [int(r[0]) for r in rows]


def parse_lineage_event(sebep: str | None) -> tuple[str, datetime | None]:
    """TKGM işlem açıklamasından işlem türünü ve geçerlilik tarihini çıkarır."""
    if not sebep:
        return "diger", None
    low = sebep.casefold()
    if "ifraz" in low or "i̇fraz" in low:
        olay = "ifraz"
    elif "tevhit" in low:
        olay = "tevhit"
    elif "nakl" in low or "nakil" in low:
        olay = "nakil"
    elif "tashih" in low:
        olay = "tashih"
    else:
        olay = "diger"
    valid = None
    m = _DATE_RE.search(sebep)
    if m:
        d, mo, y = (int(x) for x in m.groups())
        try:
            valid = datetime(y, mo, d, tzinfo=UTC)
        except ValueError:
            valid = None
    return olay, valid


def kaydet_parsel(sonuc: ParselSonuc, raw_ref: str | None = None) -> int | None:
    """Aktif parseli (geometri varsa) ``parsel`` tablosuna ekle; id döndür."""
    geom = geojson_to_multipolygon(sonuc.geometry)
    if geom is None:
        return None
    with session_scope() as s:
        row = Parsel(
            il=sonuc.il,
            ilce=sonuc.ilce,
            mahalle=sonuc.mahalle,
            mahalle_id=str(sonuc.mahalle_id),
            ada=sonuc.ada,
            parsel_no=sonuc.parsel_no,
            alan_m2=sonuc.alan_m2,
            geom=geom,
            kaynak="tkgm",
            raw_ref=raw_ref,
        )
        s.add(row)
        s.flush()
        return int(row.id)


def _str_or_none(value: object) -> str | None:
    return str(value) if value not in (None, "") else None


def kaydet_terminal_parseller(sonuc: ParselSonuc) -> int:
    """Soyağacındaki terminal (aktif, geometrili) parselleri kendi lokatörleriyle yaz.

    Pasif bir parsel sorgulandığında, arazinin **güncel** karşılığı (ifraz/nakil sonrası
    aktif parseller) buradan ``parsel`` tablosuna girer.
    """
    n = 0
    for feat in sonuc.terminal_features:
        geom = geojson_to_multipolygon(feat.get("geometry"))
        if geom is None:
            continue
        props: dict[str, Any] = feat.get("properties") or {}
        with session_scope() as s:
            s.add(
                Parsel(
                    il=props.get("ilAd"),
                    ilce=props.get("ilceAd"),
                    mahalle=props.get("mahalleAd"),
                    mahalle_id=_str_or_none(props.get("mahalleId")),
                    ada=_str_or_none(props.get("adaNo")),
                    parsel_no=_str_or_none(props.get("parselNo")),
                    alan_m2=parse_tr_number(props.get("alan")),
                    geom=geom,
                    kaynak="tkgm-soyagaci",
                )
            )
        n += 1
    return n


def kaydet_katalizor_olay(
    *,
    tur: str,
    kaynak: str,
    ozet: str | None,
    il: str | None = None,
    ilce: str | None = None,
    asama: str | None = None,
    kesinlik: float | None = None,
    kaynak_url: str | None = None,
    raw_ref: str | None = None,
    valid_time: datetime | None = None,
    geom: WKBElement | None = None,
    atla_varsa: bool = True,
    dedup_anahtar: str | None = None,
) -> tuple[int, bool]:
    """Katalizör olayını ``katalizor_olay``'a yaz (append-only, bitemporal).

    ``valid_time`` = olayın gerçekte geçerli olduğu an (karar/ÇED tarihi);
    ``knowledge_time`` = çekim (server default now()). ``dedup_anahtar`` (yoksa
    ``kaynak_url``) doğal anahtardır: aynı anahtarlı **en güncel** satırın ``ozet``'i de
    aynıysa re-fetch atlanır; ``ozet`` değiştiyse (kaynak düzeltmesi) yeni
    knowledge_time'lı satır eklenir — eski satır silinmez (append-only düzeltme zinciri).
    Döndürür: ``(id, eklendi)`` — ``eklendi=False`` ise mevcut satırın id'si döner.
    """
    anahtar = _metin(dedup_anahtar or kaynak_url, 512)
    ozet_k = _metin(ozet, 2000)
    with session_scope() as s:
        if atla_varsa and anahtar:
            mevcut = s.execute(
                select(KatalizorOlay.id, KatalizorOlay.ozet)
                .where(KatalizorOlay.dedup_anahtar == anahtar)
                .order_by(KatalizorOlay.knowledge_time.desc(), KatalizorOlay.id.desc())
                .limit(1)
            ).first()
            if mevcut is not None and mevcut[1] == ozet_k:
                return int(mevcut[0]), False
        row = KatalizorOlay(
            tur=tur,
            kaynak=kaynak,
            ozet=ozet_k,
            il=_metin(il, 64),
            ilce=_metin(ilce, 64),
            asama=_metin(asama, 16),
            kesinlik=kesinlik,
            geom=geom,
            kaynak_url=kaynak_url,
            raw_ref=raw_ref,
            valid_time=valid_time,
            dedup_anahtar=anahtar,
        )
        s.add(row)
        s.flush()
        return int(row.id), True


def kaydet_fiyat(
    *,
    tip: str,
    il: str | None,
    ilce: str | None,
    mahalle: str | None = None,
    cadde_sokak: str | None = None,
    birim_fiyat_tl_m2: float | None = None,
    fiyat_tl: float | None = None,
    m2: float | None = None,
    kaynak: str,
    kesinlik: float | None = None,
    valid_time: datetime | None = None,
    raw_ref: str | None = None,
    atla_varsa: bool = True,
    yalniz_eksik: bool = False,
) -> tuple[int, bool]:
    """Fiyat/rayiç kaydını ``fiyat``'a yaz (bitemporal, append-only).

    ``tip`` = ``rayic`` (resmi taban) | ``ilan`` (asking proxy). Doğal anahtar
    (tip+il+ilçe+mahalle+cadde+valid_time) için **en güncel** satırın değerleri de
    aynıysa ``atla_varsa`` ile atlanır; değer değiştiyse (düzeltme/revizyon) yeni
    knowledge_time'lı satır eklenir — eski satır silinmez (append-only).
    Döndürür: ``(id, eklendi)``.
    """
    with session_scope() as s:
        if atla_varsa or yalniz_eksik:
            # Eşzamanlı iki içe aktarım aynı adresin aynı sürümünü eklemesin.
            zaman = valid_time.astimezone(UTC).isoformat() if valid_time else None
            anahtar = json.dumps([tip, il, ilce, mahalle, cadde_sokak, zaman]).encode()
            kilit = int.from_bytes(hashlib.sha256(anahtar).digest()[:8], "big", signed=True)
            s.execute(select(func.pg_advisory_xact_lock(kilit)))
            ornek = func.lower(func.trim(Fiyat.kaynak)) == "placeholder"
            mevcut = s.execute(
                select(Fiyat.id, Fiyat.birim_fiyat_tl_m2, Fiyat.fiyat_tl, Fiyat.m2)
                .where(
                    Fiyat.tip == tip,
                    Fiyat.il == il,
                    Fiyat.ilce == ilce,
                    Fiyat.mahalle == mahalle,
                    Fiyat.cadde_sokak == cadde_sokak,
                    Fiyat.valid_time == valid_time,
                    ornek if kaynak.strip().lower() == "placeholder" else ~ornek,
                )
                .order_by(Fiyat.knowledge_time.desc(), Fiyat.id.desc())
                .limit(1)
            ).first()
            if mevcut is not None and (
                yalniz_eksik or tuple(mevcut[1:]) == (birim_fiyat_tl_m2, fiyat_tl, m2)
            ):
                return int(mevcut[0]), False
        row = Fiyat(
            tip=tip,
            il=il,
            ilce=ilce,
            mahalle=mahalle,
            cadde_sokak=cadde_sokak,
            birim_fiyat_tl_m2=birim_fiyat_tl_m2,
            fiyat_tl=fiyat_tl,
            m2=m2,
            kaynak=kaynak,
            kesinlik=kesinlik,
            raw_ref=raw_ref,
            valid_time=valid_time,
        )
        s.add(row)
        s.flush()
        return int(row.id), True


def kaydet_soyagaci(sonuc: ParselSonuc) -> int:
    """Pasif parselin ardıllarını ``parsel_soyagaci``'na ekle; eklenen satır sayısı."""
    if not sonuc.ardillar:
        return 0
    olay, valid = parse_lineage_event(sonuc.lineage_sebep)
    n = 0
    with session_scope() as s:
        for ardil in sonuc.ardillar:
            s.add(
                ParselSoyagaci(
                    il=sonuc.il,
                    ilce=sonuc.ilce,
                    mahalle_id=str(sonuc.mahalle_id),
                    eski_ada=sonuc.ada,
                    eski_parsel=sonuc.parsel_no,
                    yeni_ada=ardil.ada,
                    yeni_parsel=ardil.parsel,
                    olay_turu=olay,
                    valid_time=valid,
                )
            )
            n += 1
    return n
