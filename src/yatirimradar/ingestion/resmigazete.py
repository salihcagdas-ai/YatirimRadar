"""Resmi Gazete acele kamulaştırma çekici (anahtarsız / deterministik).

İşlem sırası:
1. Günlük **fihrist HTML** (`/eskiler/YYYY/MM/YYYYMMDD.htm`, charset **windows-1254**) çekilir.
2. Başlığında *"Acele Kamulaştırılması Hakkında Karar"* geçen kalemler (her biri ayrı PDF) bulunur.
3. **Başlıktan** deterministik olarak il / ilçe / karar no çıkarılır (proje açıklaması ``ozet``e).
4. Karar PDF'i ``data/raw``'a indirilir (knowledge_time izi); olay ``katalizor_olay``'a yazılır.

⚠️ Karar PDF'leri **taranmış görüntü** → ada/parsel metin katmanında YOK; OCR/Claude-vision
gerektirir (09 §2). Bu yüzden bu kademe **il/ilçe granülerliğinde** ve **düşük kesinlikte**
("izleme") katalizör üretir; ada/parsel kesinliği vision kademesine ertelenmiştir.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from types import TracebackType
from urllib.parse import urljoin

import structlog
from bs4 import BeautifulSoup

from yatirimradar.ingestion.http import PoliteClient, save_raw
from yatirimradar.store.repository import kaydet_katalizor_olay

log = structlog.get_logger(__name__)

RG_BASE = "https://www.resmigazete.gov.tr"

# Günlük fihristteki karar başlığından karar numarasını çıkar.
_KARAR_NO_RE = re.compile(r"Karar\s+Sayısı:?\s*(\d+)")
# İl: büyük harfle başlayan tek sözcük + "İli"/"İlinde" (il adları tek sözcük; ortada da olabilir).
_IL_RE = re.compile(r"([A-ZÇĞİÖŞÜ][A-Za-zÇĞİÖŞÜçğıöşüâîû]+)\s+İl(?:i|inde)\b")
# İlçe: "{İl} İli, {ilçe(ler)} İlçe..." → "İli/İlinde" ile "İlçe" arasını yakala.
_ILCE_RE = re.compile(r"İl(?:i|inde)[\s,]+(.+?)\s+İlçe")
_ILCE_AYIRAC_RE = re.compile(r"\s+ve\s+|\s+ile\s+|[,/]")
# Başlık başındaki süsleme (–– • · - …) temizliği.
_BASLIK_ONEK_RE = re.compile(r"^[\s–—\-•·.]+")


def _norm(s: str) -> str:
    """Türkçe harfleri ASCII'ye çevirip küçült (anahtar kelime eşlemesi için)."""
    table = str.maketrans("İıŞşĞğÇçÖöÜü", "iissggccoouu")
    return s.translate(table).lower()


def fihrist_url(tarih: date) -> str:
    """Verilen tarihin fihrist HTML URL'i."""
    return f"{RG_BASE}/eskiler/{tarih.year}/{tarih.month:02d}/{tarih:%Y%m%d}.htm"


@dataclass(frozen=True)
class KamulastirmaKalem:
    """Fihristteki tek acele kamulaştırma kararı (başlıktan çıkarılmış)."""

    tarih: date
    pdf_url: str
    baslik: str
    karar_no: str | None = None
    il: str | None = None
    ilce: str | None = None

    @property
    def kesinlik(self) -> float:
        """İl/ilçe granülerliğine göre düşük güven (ada/parsel vision'a ertelendi)."""
        if self.il and self.ilce:
            return 0.4
        if self.il:
            return 0.3
        return 0.15


def is_acele_kamulastirma(baslik: str) -> bool:
    """Başlık bir acele kamulaştırma kararı mı?"""
    return "acele kamula" in _norm(baslik)


def _parse_ilce(baslik: str) -> str | None:
    """Başlıktan ilçe(ler)i çıkar; birden çoksa ``", "`` ile birleştir (64'e kırp)."""
    m = _ILCE_RE.search(baslik)
    if not m:
        return None
    parcalar = [p.strip() for p in _ILCE_AYIRAC_RE.split(m.group(1))]
    # Yalnız özel ad gibi görünen (büyük harfle başlayan) parçaları al.
    adlar = [p for p in parcalar if p and p[0].isupper()]
    if not adlar:
        return None
    return ", ".join(adlar)[:64]


def parse_baslik(baslik: str) -> tuple[str | None, str | None, str | None]:
    """Başlık → ``(il, ilce, karar_no)`` (deterministik, en-iyi-çaba)."""
    il_m = _IL_RE.search(baslik)
    il = il_m.group(1) if il_m else None
    karar_m = _KARAR_NO_RE.search(baslik)
    karar_no = karar_m.group(1) if karar_m else None
    return il, _parse_ilce(baslik), karar_no


def parse_fihrist(html: bytes, fihrist_adresi: str, tarih: date) -> list[KamulastirmaKalem]:
    """Fihrist HTML (windows-1254 byte) → acele kamulaştırma kalemleri.

    Saf fonksiyon (ağ yok): byte içeriği BeautifulSoup'a verilir (meta charset'ten
    windows-1254 otomatik çözülür); ``.pdf``'e işaret eden ve başlığı acele
    kamulaştırma olan ``<a>`` etiketleri ayrıştırılır.
    """
    soup = BeautifulSoup(html, "lxml")
    kalemler: list[KamulastirmaKalem] = []
    gorulen: set[str] = set()
    for a in soup.find_all("a"):
        href = a.get("href") or ""
        if not isinstance(href, str) or not href.lower().endswith(".pdf"):
            continue
        baslik = " ".join(a.get_text(" ", strip=True).split())
        if not is_acele_kamulastirma(baslik):
            continue
        pdf_url = urljoin(fihrist_adresi, href)
        if pdf_url in gorulen:
            continue
        gorulen.add(pdf_url)
        baslik = _BASLIK_ONEK_RE.sub("", baslik).strip()
        il, ilce, karar_no = parse_baslik(baslik)
        kalemler.append(
            KamulastirmaKalem(
                tarih=tarih,
                pdf_url=pdf_url,
                baslik=baslik,
                karar_no=karar_no,
                il=il,
                ilce=ilce,
            )
        )
    return kalemler


@dataclass
class GunRaporu:
    """Bir günün acele kamulaştırma tarama özeti."""

    tarih: date
    fihrist_kalem: int = 0
    indirilen_pdf: int = 0
    il_bulunan: int = 0
    ilce_bulunan: int = 0
    yazilan: int = 0
    atlanan: int = 0
    hata: int = 0
    kalemler: list[KamulastirmaKalem] = field(default_factory=list)


class ResmiGazeteClient:
    """Fihrist + karar PDF indirici (nazik HTTP).

    Not: ``resmigazete.gov.tr`` yerel DNS'te sorunsuz çözülür (TKGM zone'u gibi
    reddedilmez), bu yüzden public-DNS override'a gerek yok (``dns_overrides={}``).
    """

    def __init__(self, client: PoliteClient | None = None) -> None:
        self._owns = client is None
        self._client = client or PoliteClient(dns_overrides={})

    def fihrist_bytes(self, tarih: date) -> bytes:
        r = self._client.get(fihrist_url(tarih))
        r.raise_for_status()
        return r.content

    def indir(self, url: str) -> bytes:
        r = self._client.get(url)
        r.raise_for_status()
        return r.content

    def close(self) -> None:
        if self._owns:
            self._client.close()

    def __enter__(self) -> ResmiGazeteClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


def tara_gun(
    tarih: date,
    client: ResmiGazeteClient | None = None,
    indir_pdf: bool = True,
    kaydet: bool = True,
) -> GunRaporu:
    """Bir günün fihristini tara → acele kamulaştırma olaylarını ``katalizor_olay``'a yaz.

    Yazılan her olay: ``tur="kamulastirma"``, ``asama="karar"``, il/ilçe granül,
    ``kesinlik`` düşük, ``geom=None`` (ada/parsel vision'a ertelendi),
    ``valid_time``=karar/yayım tarihi, ``knowledge_time``=çekim.
    """
    rapor = GunRaporu(tarih=tarih)
    rg = client or ResmiGazeteClient()
    valid_time = datetime(tarih.year, tarih.month, tarih.day, tzinfo=UTC)
    try:
        adres = fihrist_url(tarih)
        html = rg.fihrist_bytes(tarih)
        save_raw(html, source="resmigazete", name=f"fihrist_{tarih:%Y%m%d}.htm")
        kalemler = parse_fihrist(html, adres, tarih)
        rapor.fihrist_kalem = len(kalemler)
        rapor.kalemler = kalemler

        for kalem in kalemler:
            if kalem.il:
                rapor.il_bulunan += 1
            if kalem.ilce:
                rapor.ilce_bulunan += 1

            raw_ref: str | None = None
            if indir_pdf:
                try:
                    pdf = rg.indir(kalem.pdf_url)
                    raw_ref = str(
                        save_raw(
                            pdf, source="resmigazete", name=kalem.pdf_url.rsplit("/", 1)[-1]
                        )
                    )
                    rapor.indirilen_pdf += 1
                except Exception as exc:  # noqa: BLE001 - tek PDF hatası taramayı durdurmasın
                    rapor.hata += 1
                    log.warning("pdf_download_failed", url=kalem.pdf_url, error=str(exc))

            if kaydet:
                _, eklendi = kaydet_katalizor_olay(
                    tur="kamulastirma",
                    kaynak="resmigazete",
                    ozet=kalem.baslik,
                    il=kalem.il,
                    ilce=kalem.ilce,
                    asama="karar",
                    kesinlik=kalem.kesinlik,
                    kaynak_url=kalem.pdf_url,
                    raw_ref=raw_ref,
                    valid_time=valid_time,
                )
                if eklendi:
                    rapor.yazilan += 1
                else:
                    rapor.atlanan += 1
    finally:
        if client is None:
            rg.close()
    return rapor
