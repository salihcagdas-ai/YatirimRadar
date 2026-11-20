"""Arka plan (yavaş, kaldığı yerden devam eden) geocode işçisi.

DB'de geometrisi olmayan ÇED olaylarını, ``ozet`` içine gömülü ``@ PROJE YERİ``
lokatöründen çözer (TKGM). Tasarım ilkeleri:

- **Yavaş / nazik:** her TKGM isteği arası gecikme; bir partide en çok ``adet`` **deneme**.
- **Hedefli:** yalnız ``ozet``'inde ``@ PROJE YERİ`` olan adaylar çekilir (``LIKE '% @ %'``);
  ada/parsel'i parse edilemeyen olaylar TKGM'ye hiç sorulmaz, deneme bütçesini yemez.
- **Kaldığı yerden devam:** yalnız ``geom IS NULL`` olanları işler → tekrar koşmak sürdürür.
- **403/WAF dayanıklı:** "parsel bulunamadı" (404) gerçek sonuçtur, sıradakine geçilir;
  yalnız gerçek rate-limit (403/429 → ``TkgmRateLimitError``) partiyi erken durdurur,
  ilerleme kaybolmaz.

İlerleme ``durum`` (yatirimradar/durum.py) ile görülebilir.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace

import structlog
from sqlalchemy import select

from yatirimradar.config import PROJECT_ROOT
from yatirimradar.geocode.cozucu import (
    GeocoderProtokol,
    TkgmGeocoder,
    TkgmRateLimitError,
    temsili_geometri,
)
from yatirimradar.geocode.yer import YerLokator, parse_yer
from yatirimradar.ingestion.http import PoliteClient
from yatirimradar.ingestion.tkgm import TkgmClient
from yatirimradar.store.db import session_scope
from yatirimradar.store.models import KatalizorOlay
from yatirimradar.store.repository import geojson_to_multipolygon, guncelle_katalizor_geom

log = structlog.get_logger(__name__)

GEOCODE_KESINLIK = 0.75

# ÇED ozet'i: "SEKTÖR — PROJE (SAHİBİ) @ PROJE YERİ [SONUÇ]" → @ ile [ arasını al.
_YER_RE = re.compile(r" @ (.+?)(?: \[[^\]]*\])?$")

# Başarısız (TKGM'de çözülemeyen) olay id'leri burada hatırlanır → sonraki turlar atlar.
# Böylece işçi aynı çözülemeyenlere takılmaz, hep YENİ adaylara ilerler (migration'sız).
# Sıfırlamak (hepsini tekrar denemek) için dosyayı sil ya da CLI --tumunu-dene.
_BASARISIZ_DOSYA = PROJECT_ROOT / "data" / "processed" / "geocode_basarisiz.json"


def _basarisiz_yukle() -> set[int]:
    """Daha önce denenip çözülemeyen olay id kümesi (yoksa boş)."""
    try:
        return {int(x) for x in json.loads(_BASARISIZ_DOSYA.read_text("utf-8"))}
    except (OSError, ValueError, TypeError):
        return set()


def _basarisiz_kaydet(ids: set[int]) -> None:
    """Çözülemeyen id kümesini kalıcılaştır (sonraki turlar atlasın)."""
    _BASARISIZ_DOSYA.parent.mkdir(parents=True, exist_ok=True)
    _BASARISIZ_DOSYA.write_text(json.dumps(sorted(ids)), "utf-8")


def lokator_ozetten(ozet: str | None, ilce_kayit: str | None = None) -> YerLokator | None:
    """ÇED ``ozet``'inden ``@ PROJE YERİ`` kısmını çıkar → ``YerLokator``."""
    if not ozet:
        return None
    m = _YER_RE.search(ozet)
    if not m:
        return None
    lok = parse_yer(m.group(1))
    if not lok.ilce and ilce_kayit:  # ozet'te ilçe yoksa kayıtlı ilçeyi kullan
        lok = replace(lok, ilce=ilce_kayit)
    return lok


@dataclass
class ArkaplanRaporu:
    """Bir geocode partisinin özeti."""

    bakilan: int = 0
    geocode_denenen: int = 0
    cozulen: int = 0
    atlanan: int = 0  # ada/parsel yok → TKGM'ye sorulmadı
    erken_durdu: bool = False
    mesaj: str | None = None  # erken durdurma nedeni (TKGM'nin döndürdüğü gerçek metin)


def _bekleyenler(
    il: str | None, haric: set[int] | None = None
) -> list[tuple[int, str | None, str | None, str | None]]:
    """Geometrisi olmayan, ``ozet``'inde ``@ PROJE YERİ`` bulunan ÇED olayları (id sıralı).

    ``LIKE '% @ %'`` adayları daraltır → eski/yer'siz arşiv satırları taranıp deneme bütçesini
    boşa harcamaz. ``haric`` = daha önce çözülemeyen id'ler (atlanır → her tur yeni adaylar).
    Ada/parsel parse refine'ı çağıranda (``lokator_ozetten``) yapılır.
    """
    stmt = (
        select(KatalizorOlay.id, KatalizorOlay.il, KatalizorOlay.ilce, KatalizorOlay.ozet)
        .where(
            KatalizorOlay.tur == "ced",
            KatalizorOlay.geom.is_(None),
            KatalizorOlay.ozet.like("% @ %"),
        )
        .order_by(KatalizorOlay.id)
    )
    if il:
        stmt = stmt.where(KatalizorOlay.il == il)
    if haric:
        stmt = stmt.where(KatalizorOlay.id.notin_(haric))
    with session_scope() as s:
        return [(int(r[0]), r[1], r[2], r[3]) for r in s.execute(stmt)]


def geocode_partisi(
    adet: int = 200,
    delay: float = 1.0,
    il: str | None = None,
    geocoder: GeocoderProtokol | None = None,
    denenen_atla: bool = True,
) -> ArkaplanRaporu:
    """Geometrisi olmayan ÇED olaylarını geocode et — partide en çok ``adet`` deneme.

    ``adet`` taranan satırı değil **gerçek TKGM denemesini** sınırlar (ağ maliyeti budur);
    ada/parsel'i parse edilemeyen adaylar bütçeyi yemeden atlanır. Gerçek rate-limit
    (``TkgmRateLimitError``) partiyi erken durdurur; "bulunamadı" (404) sıradakine geçilir.
    ``denenen_atla`` (varsayılan): bu turda çözülemeyen olaylar kalıcı kümeye eklenir →
    sonraki turlar atlar, işçi hep YENİ adaylara ilerler (aynı çözülemeyenlere takılmaz).
    """
    rapor = ArkaplanRaporu()
    basarisiz = _basarisiz_yukle() if denenen_atla else set()
    yeni_basarisiz = set(basarisiz)
    gc = geocoder or TkgmGeocoder(TkgmClient(PoliteClient(delay_seconds=delay)))
    try:
        for oid, o_il, ilce, ozet in _bekleyenler(il, basarisiz):
            if rapor.geocode_denenen >= adet:
                break
            rapor.bakilan += 1
            lok = lokator_ozetten(ozet, ilce)
            if lok is None or not lok.geocode_edilebilir:
                rapor.atlanan += 1
                continue
            try:
                sonuc = gc.coz(o_il, lok)
            except TkgmRateLimitError as exc:
                rapor.erken_durdu = True  # 403 → ilerleme korunur, sonra kaldığı yerden devam
                rapor.mesaj = str(exc)
                break
            rapor.geocode_denenen += 1
            cozuldu = False
            if sonuc is not None:
                geojson = temsili_geometri(sonuc)
                geom = geojson_to_multipolygon(geojson) if geojson else None
                if geom is not None and guncelle_katalizor_geom(
                    oid, geom, kesinlik=GEOCODE_KESINLIK,
                    il=sonuc.il or o_il, ilce=sonuc.ilce or ilce,
                ):
                    rapor.cozulen += 1
                    cozuldu = True
            if not cozuldu:  # bulunamadı/çözülemedi → kalıcı işaretle (sonraki turlar atlar)
                yeni_basarisiz.add(oid)
    finally:
        if geocoder is None and isinstance(gc, TkgmGeocoder):
            gc.close()
    if denenen_atla and yeni_basarisiz != basarisiz:
        _basarisiz_kaydet(yeni_basarisiz)
    return rapor
