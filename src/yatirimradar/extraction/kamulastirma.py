"""Acele kamulaştırma kararı → ada/parsel çıkarımı (vision) → geometri.

R.Gazete karar PDF'leri **taranmış görüntü**; ada/parsel tablosu (EK LİSTE) gömülü
resimlerde. Çıkarım iki kademeli:

1. **Yerel karar dosyası:** Tarama tablolarından okunan ada/parsel kayıtları
   ``data/seed/rg_kamulastirma_parseller.json`` yolundan yüklenir.
2. **API vision (gelecek):** ``ANTHROPIC_API_KEY`` gelince ``pdf_sayfa_gorselleri`` ile
   sayfalar render edilip Claude vision'a verilir → aynı ``KararKaydi`` üretilir.

Her iki kademe de ``KararKaydi`` üretir; ``uygula`` bunu mevcut ``katalizor_olay``
(kamulaştırma) satırına **gerçek TKGM geometrisi** olarak işler (geocode).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import structlog

from yatirimradar.config import PROJECT_ROOT
from yatirimradar.geocode.cozucu import GeocoderProtokol, TkgmRateLimitError, temsili_geometri
from yatirimradar.geocode.yer import YerLokator
from yatirimradar.store.repository import (
    geojsonlar_to_multipolygon,
    guncelle_katalizor_geom,
    kamulastirma_olaylari,
)

log = structlog.get_logger(__name__)

SEED_YOL = PROJECT_ROOT / "data" / "seed" / "rg_kamulastirma_parseller.json"
# Parsel düzeyi geocode'lu kamulaştırma = güçlü + kesin sinyal.
GEOCODE_KESINLIK = 0.8
# TKGM'de ada'sız köy parselleri ada=0 ile sorgulanır.
ADASIZ = "0"


@dataclass(frozen=True)
class ParselRef:
    """Tek parsel + tam konumu (kararlar çok-ilçe/çok-mahalle olabilir)."""

    il: str | None
    ilce: str | None
    mahalle: str | None
    ada: str | None
    parsel: str


@dataclass(frozen=True)
class KararKaydi:
    """Bir kararın EK LİSTE'sinden çıkarılmış parseller (her biri tam konumlu)."""

    karar_no: str
    parseller: list[ParselRef]

    @property
    def il(self) -> str | None:
        return self.parseller[0].il if self.parseller else None


def seed_yukle(yol: Path | None = None) -> list[KararKaydi]:
    """Seed JSON → ``KararKaydi`` listesi. İki form: tek-konum ``parseller`` ya da ``gruplar``.

    '_' ile başlayan meta anahtarlar atlanır; eksik ilçe/mahalle karar-düzeyinden devralınır.
    """
    if yol is None:
        if not SEED_YOL.exists():
            return []
        yol = SEED_YOL
    data = json.loads(yol.read_text(encoding="utf-8"))
    kayitlar: list[KararKaydi] = []
    for karar_no, k in data.items():
        if karar_no.startswith("_") or not isinstance(k, dict):
            continue
        il = k.get("il")
        refs: list[ParselRef] = []
        for p in k.get("parseller", []):  # tek-konum form
            if not p.get("parsel"):
                continue
            refs.append(
                ParselRef(
                    il=p.get("il") or il,
                    ilce=p.get("ilce") or k.get("ilce"),
                    mahalle=p.get("mahalle") or k.get("mahalle"),
                    ada=p.get("ada"),
                    parsel=str(p["parsel"]),
                )
            )
        for g in k.get("gruplar", []):  # çok-konum form
            for parsel in g.get("parseller", []):
                refs.append(
                    ParselRef(
                        il=g.get("il") or il,
                        # Eksik ilçe/mahalle karar-düzeyinden devral (tek-konum formla tutarlı).
                        ilce=g.get("ilce") or k.get("ilce"),
                        mahalle=g.get("mahalle") or k.get("mahalle"),
                        ada=g.get("ada"),
                        parsel=str(parsel),
                    )
                )
        kayitlar.append(KararKaydi(karar_no=karar_no, parseller=refs))
    return kayitlar


def pdf_sayfa_gorselleri(pdf_yol: Path, dpi: int = 150) -> list[bytes]:
    """Karar PDF sayfalarını PNG'ye render et (vision tier girdisi). PyMuPDF gerekir.

    Seed kademesi bunu kullanmaz; ``ANTHROPIC_API_KEY``'li otomatik vision tier içindir.
    """
    try:
        import fitz  # PyMuPDF  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("PDF rendering requires PyMuPDF. Run 'uv sync --extra heavy'.") from exc
    doc = fitz.open(pdf_yol)
    return [doc[i].get_pixmap(dpi=dpi).tobytes("png") for i in range(doc.page_count)]


@dataclass
class CikarimRaporu:
    """Kamulaştırma parsel çıkarımı + geocode özeti."""

    karar_sayisi: int = 0
    olay_eslesen: int = 0
    parsel_cozulen: int = 0
    geom_yazilan: int = 0
    eslenmeyen: list[str] = field(default_factory=list)
    rate_limit: bool = False  # TKGM 403 → erken durdu (kalan kararlar işlenmedi)
    mesaj: str | None = None  # TKGM'nin döndürdüğü gerçek metin (ör. günlük kota)


def uygula(
    kayitlar: list[KararKaydi],
    geocoder: GeocoderProtokol,
    kaydet: bool = True,
) -> CikarimRaporu:
    """Her karar için parselleri geocode et → eşleşen kamulaştırma olayına geometri yaz.

    TKGM rate-limit'inde (``TkgmRateLimitError``) zarif durur: işlenmiş kararların sonucu
    korunur, kalanlar TKGM soğuyunca tekrar koşuda tamamlanır (``rate_limit=True``).
    """
    rapor = CikarimRaporu(karar_sayisi=len(kayitlar))
    for kayit in kayitlar:
        geojsonlar: list[dict[str, object] | None] = []
        try:
            for ref in kayit.parseller:
                lok = YerLokator(
                    il=ref.il, ilce=ref.ilce, mahalle=ref.mahalle,
                    ada=ref.ada or ADASIZ, parsel=ref.parsel,
                )
                sonuc = geocoder.coz(ref.il, lok)
                if sonuc is not None:
                    geojsonlar.append(temsili_geometri(sonuc))
                    rapor.parsel_cozulen += 1
        except TkgmRateLimitError as exc:
            rapor.rate_limit = True  # bu kararın kısmi geom'unu yazma, tümüyle dur
            rapor.mesaj = str(exc)
            break

        geom = geojsonlar_to_multipolygon(geojsonlar)
        olaylar = kamulastirma_olaylari(kayit.karar_no)
        if not olaylar:
            rapor.eslenmeyen.append(kayit.karar_no)
            continue
        rapor.olay_eslesen += len(olaylar)
        if geom is not None and kaydet:
            for olay_id in olaylar:
                if guncelle_katalizor_geom(olay_id, geom, kesinlik=GEOCODE_KESINLIK):
                    rapor.geom_yazilan += 1
    return rapor
