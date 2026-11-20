"""Fırsat skoru (ilk geçiş, saf/deterministik).

Tez (08, kickoff): **taze + yüksek-kesinlikli katalizöre yakın + ucuz** (düşük rayiç)
arsa/tarla = en yüksek değer-açığı fırsatı. Skor altı bileşenin çarpımı (0..1):

- ``tur_agirlik`` : kamulaştırma (inşaat yakın) > ÇED (planlı) sinyal gücü.
- ``kesinlik``    : olayın uzamsal/güven skoru (geocode → parsel düzeyi yüksek).
- ``tazelik``     : karar ne kadar yeni (piyasa henüz fiyatlamamış olabilir).
- ``ucuzluk``     : bölge rayici ne kadar düşük (göreli yukarı potansiyel büyük).
- ``yogunluk``    : aynı ilçedeki katalizör birikimi (çok katalizör = sıcak bölge,
  tek başına bir olaydan güçlü sinyal). Yalnız çarpan; tek olayı sıfırlamaz (taban 0.6).
- ``buyukluk``    : projenin ölçeği (büyük GES/maden/kamulaştırma > küçük tesis).
  ÇED özetinden kapasite (MW / ton/yıl / alan), kamulaştırmada parsel alanından (m²)
  türetilir; log ölçekli çarpan (taban 0.6). Veri yoksa nötr (1.0) — eksik veri cezalandırılmaz.

Skor saklanmaz — ``katalizor_olay`` + ``fiyat`` üzerinden türetilir (bkz. scoring/sirala).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from yatirimradar.sayi import parse_tr_number

# Tür sinyal ağırlıkları (kamulaştırma = en güçlü, inşaat/edinim yakın).
TUR_AGIRLIK: dict[str, float] = {"kamulastirma": 1.0, "ced": 0.7, "kgm_yol": 0.9, "osb": 0.8}
TUR_AGIRLIK_VARSAYILAN = 0.5

REF_M2_TL = 300.0  # "ucuz arsa" eşiği: bunun altındaki rayiç tam ucuzluk (=1).
TAZELIK_TAU = 6.0  # üstel azalım zaman sabiti (yıl); yarı-ömür ≈ τ·ln2 ≈ 4.2 yıl.
TAZELIK_TABAN = 0.1  # çok eski katalizör tamamen sıfırlanmaz (büyük olay izini korusun).
YOGUNLUK_REF = 8.0  # ilçede bu kadar katalizör → tam yoğunluk (=1).
YOGUNLUK_TABAN = 0.6  # tek katalizörün cezası bu kadar (sıfırlanmaz).

# Büyüklük: her birim için "tipik küçük proje" referansı (bu değerde ölçek-oranı=1 → taban).
KAPASITE_MW_REF = 5.0  # MW (GES/RES güç) — bunun altı tam küçük.
KAPASITE_TON_YIL_REF = 50_000.0  # ton/yıl (maden/üretim/atık kapasitesi).
KAPASITE_SAYI_REF = 20_000.0  # adet/dönem · baş (hayvancılık/üretim sayısı).
ALAN_REF_M2 = 100_000.0  # m² (≈10 ha proje/parsel alanı).
BUYUKLUK_TABAN = 0.6  # en küçük projenin çarpanı (sıfırlanmaz).
BUYUKLUK_TAVAN = 100.0  # ölçek-oranı bu katsayıda → tam 1.0 (log ölçek tepe noktası).

# "ton/{periyot}" → ton/yıl çevrim çarpanı (kapasite yıllığa indirgenir).
_TON_PERIYOT_CARPAN = {
    "yıl": 1.0, "yil": 1.0, "ay": 12.0, "hafta": 52.0, "gün": 365.0, "gun": 365.0, "saat": 8760.0,
}


def tur_agirlik(tur: str | None) -> float:
    return TUR_AGIRLIK.get(tur or "", TUR_AGIRLIK_VARSAYILAN)


def tazelik(valid_time: datetime | None, simdi: datetime | None = None) -> float:
    """Yenilik skoru (üstel azalım): bugün ~1, eskidikçe ``exp(-yıl/τ)`` ile yumuşak söner.

    Doğrusal sert kesim yerine (8 yıl 1 gün → 0) üstel: yarı-ömür ≈ 4.2 yıl, ``TAZELIK_TABAN``
    tabanı sayesinde çok eski ama büyük katalizör tamamen sıfırlanmaz. Gelecek tarih → 1.
    """
    if valid_time is None:
        return 0.5  # tarih yok → nötr
    simdi = simdi or datetime.now(UTC)
    yil = (simdi - valid_time).days / 365.25
    if yil <= 0:  # gelecekteki tarih (veri hatası) ya da bugün → tam taze
        return 1.0
    return TAZELIK_TABAN + (1.0 - TAZELIK_TABAN) * math.exp(-yil / TAZELIK_TAU)


def ucuzluk(rayic_tl_m2: float | None) -> float:
    """Ucuzluk skoru: rayiç ``REF_M2_TL`` altıysa 1, pahalılaştıkça 0'a iner. Veri yoksa nötr."""
    if rayic_tl_m2 is None or rayic_tl_m2 <= 0:
        return 0.5
    return min(1.0, REF_M2_TL / rayic_tl_m2)


def yogunluk(ilce_katalizor_sayisi: int) -> float:
    """Yoğunluk çarpanı: ilçedeki katalizör sayısı arttıkça ``YOGUNLUK_TABAN``→1.0.

    Tek olay → taban (0.6); ``YOGUNLUK_REF`` (8) ve üstü → 1.0. Çarpan olduğu için
    sıcak bölgeleri öne çıkarır ama yalnız olayı sıfırlamaz.
    """
    n = max(0, ilce_katalizor_sayisi)
    oran = min(1.0, n / YOGUNLUK_REF)
    return YOGUNLUK_TABAN + (1.0 - YOGUNLUK_TABAN) * oran


# Ölçek sinyali desenleri (ÇED özet metninden). Sayı TR formatı: "41,877" / "1.500.000".
_MW_RE = re.compile(r"(\d[\d.,]*)\s*MW")  # MW/MWe/MWm/MWh/MWp (güç) — büyük harf MW
# ton/{yıl,ay,hafta,gün,saat} — periyot group(2) ile yıllığa çevrilir (maden/atık/üretim).
_TON_RE = re.compile(r"(\d[\d.,]*)\s*ton\s*/\s*(y[ıi]l|ay|hafta|g[üu]n|saat)", re.IGNORECASE)
# Sayı kapasitesi: "120.000 adet/dönem" (kümes) · "50.000 baş" (hayvancılık).
_SAYI_RE = re.compile(
    r"(\d[\d.,]*)\s*(?:adet\s*/\s*(?:d[öo]nem|y[ıi]l|g[üu]n)|ba[şs])\b", re.IGNORECASE
)
_HEKTAR_RE = re.compile(r"(\d[\d.,]*)\s*(?:hektar|ha)\b", re.IGNORECASE)
_M2_RE = re.compile(r"(\d[\d.,]*)\s*m(?:2|²)", re.IGNORECASE)
_DONUM_RE = re.compile(r"(\d[\d.,]*)\s*d[öo]n[üu]m", re.IGNORECASE)


def _tr_sayi(s: str) -> float | None:
    """TR sayı metnini float'a çevir ("41,877"→41.877, "1.500.000"→1.5M; bkz. sayi.py)."""
    return parse_tr_number(s)


def _en_buyuk(rx: re.Pattern[str], metin: str, faktor: float = 1.0) -> float | None:
    """``metin`` içinde ``rx``'in yakaladığı en büyük sayıyı (``faktor`` ile ölçekli) döndür."""
    en: float | None = None
    for m in rx.finditer(metin):
        v = _tr_sayi(m.group(1))
        if v is not None and (en is None or v * faktor > en):
            en = v * faktor
    return en


def _en_ton_yil(metin: str) -> float | None:
    """``ton/{periyot}`` eşleşmelerini yıllığa çevirip en büyük ton/yıl değerini döndür."""
    en: float | None = None
    for m in _TON_RE.finditer(metin):
        v = _tr_sayi(m.group(1))
        if v is None:
            continue
        yillik = v * _TON_PERIYOT_CARPAN.get(m.group(2).lower(), 1.0)
        if en is None or yillik > en:
            en = yillik
    return en


def kapasite_olcek(metin: str | None) -> float | None:
    """Özet metninden proje ölçeğini referansa göre normalize edilmiş **oran** olarak çıkar.

    Sinyaller: MW (güç) · ton/{yıl,ay,gün,saat...} (üretim/atık, yıllığa çevrilir) ·
    adet/dönem & baş (hayvancılık sayısı) · alan (ha / m² / dönüm → m²). Her biri kendi
    referansına bölünür; en güçlü sinyalin oranı döner (ör. 50 MW → 50/5 = 10.0).
    Hiçbir ölçek bulunmazsa ``None`` (büyüklük çarpanı nötr kalır; ruhsat/kV no'ları gürültü).
    """
    if not metin:
        return None
    oranlar: list[float] = []
    mw = _en_buyuk(_MW_RE, metin)
    if mw is not None:
        oranlar.append(mw / KAPASITE_MW_REF)
    ton = _en_ton_yil(metin)
    if ton is not None:
        oranlar.append(ton / KAPASITE_TON_YIL_REF)
    sayi = _en_buyuk(_SAYI_RE, metin)
    if sayi is not None:
        oranlar.append(sayi / KAPASITE_SAYI_REF)
    alanlar = [
        a
        for a in (
            _en_buyuk(_HEKTAR_RE, metin, 10_000.0),  # ha → m²
            _en_buyuk(_M2_RE, metin),
            _en_buyuk(_DONUM_RE, metin, 1_000.0),  # dönüm → m²
        )
        if a is not None
    ]
    if alanlar:
        oranlar.append(max(alanlar) / ALAN_REF_M2)
    return max(oranlar) if oranlar else None


def alan_olcek(alan_m2: float | None) -> float | None:
    """Parsel/proje alanını (m²) referans alana göre orana çevir (kamulaştırma geom için)."""
    if alan_m2 is None or alan_m2 <= 0:
        return None
    return alan_m2 / ALAN_REF_M2


def olcek_belirle(ozet: str | None, tur: str | None, alan_m2: float | None = None) -> float | None:
    """Olayın büyüklük ölçeğini belirle: önce özet kapasitesi, yoksa kamulaştırmada parsel alanı.

    ÇED → özet metnindeki kapasite (MW/ton/alan). Kamulaştırma özeti genelde ölçek
    taşımaz → çözülmüş geometrinin alanı (m²) kullanılır.
    """
    olcek = kapasite_olcek(ozet)
    if olcek is None and tur == "kamulastirma":
        olcek = alan_olcek(alan_m2)
    return olcek


def buyukluk(olcek: float | None) -> float:
    """Büyüklük çarpanı: ölçek-oranını log ölçekte ``BUYUKLUK_TABAN``→1.0'a eşle.

    ``olcek`` (referansa göre oran): 1 → taban (0.6), ``BUYUKLUK_TAVAN`` (100×) ve üstü → 1.0.
    Veri yoksa (``None``) nötr 1.0 döner — eksik ölçek bilgisi cezalandırılmaz.
    """
    if olcek is None or olcek <= 0:
        return 1.0
    oran = math.log10(olcek) / math.log10(BUYUKLUK_TAVAN)
    return BUYUKLUK_TABAN + (1.0 - BUYUKLUK_TABAN) * max(0.0, min(1.0, oran))


@dataclass(frozen=True)
class SkorBilesen:
    """Skor ve bileşenleri (açıklanabilirlik için ayrı ayrı tutulur)."""

    tur_agirlik: float
    kesinlik: float
    tazelik: float
    ucuzluk: float
    yogunluk: float = 1.0
    buyukluk: float = 1.0

    @property
    def skor(self) -> float:
        return (
            self.tur_agirlik
            * self.kesinlik
            * self.tazelik
            * self.ucuzluk
            * self.yogunluk
            * self.buyukluk
        )


def skorla(
    tur: str | None,
    kesinlik: float | None,
    valid_time: datetime | None,
    rayic_tl_m2: float | None,
    simdi: datetime | None = None,
    ilce_katalizor_sayisi: int = 1,
    buyukluk_olcek: float | None = None,
) -> SkorBilesen:
    """Bir katalizör olayını rayiç + ilçe yoğunluğu + proje büyüklüğü bağlamında skorla (0..1)."""
    return SkorBilesen(
        tur_agirlik=tur_agirlik(tur),
        kesinlik=kesinlik if kesinlik is not None else 0.3,
        tazelik=tazelik(valid_time, simdi),
        ucuzluk=ucuzluk(rayic_tl_m2),
        yogunluk=yogunluk(ilce_katalizor_sayisi),
        buyukluk=buyukluk(buyukluk_olcek),
    )
