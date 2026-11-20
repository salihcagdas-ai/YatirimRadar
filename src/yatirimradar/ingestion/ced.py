"""ÇED kararları çekici (anahtarsız / deterministik) — yapısal XLSX.

ÇED kararları Bakanlığın XLSX cetvellerinden okunur. Kolonlar başlığa göre eşlenir:
``KARAR NO · KARAR TARİHİ · SEKTÖR · PROJENİN ADI · PROJE SAHİBİ · İLİ · PROJE YERİ ·
RAPORU HAZIRLAYAN KURUM · KARAR SONUCU``.

Koordinat yoktur → **il + proje-yeri** granülerliğinde, düşük kesinlikte ("izleme")
katalizör (R.Gazete ile aynı strateji). ``valid_time`` = KARAR TARİHİ.
"""

from __future__ import annotations

import hashlib
import io
import warnings
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from types import TracebackType

import openpyxl
import structlog

from yatirimradar.geocode.cozucu import TkgmGeocoder, TkgmRateLimitError, temsili_geometri
from yatirimradar.geocode.yer import parse_yer
from yatirimradar.ingestion.http import PoliteClient, save_raw
from yatirimradar.store.repository import geojson_to_multipolygon, kaydet_katalizor_olay

log = structlog.get_logger(__name__)

# Lokatör TKGM parseline çözüldüğünde (parsel düzeyi konum) kesinlik bu değere yükselir.
GEOCODE_KESINLIK = 0.75

# 1993–2024 arşivi (webdosya, normal UA + verify ile erişilebilir; backtest kaynağı).
CED_OLUMLU_ARSIV_URL = (
    "https://webdosya.csb.gov.tr/db/ced/menu/"
    "93_2024_cedolumlu_-kopya---kopya_20250616051850.xlsx"
)
# 2025+ güncel e-ÇED kararları.
CED_OLUMLU_GUNCEL_URL = "https://eced.csb.gov.tr/images/ced_olumlu_projeler.xlsx"

# Kolon başlığı (normalize) → mantıksal alan.
_KOLON_ESLEME = {
    "karar no": "karar_no",
    "karar tarihi": "tarih",
    "sektor": "sektor",
    "projenin adi": "proje_adi",
    "proje sahibi": "proje_sahibi",
    "ili": "il",
    "ilcesi": "ilce",  # eced 2025+ ayrı ilçe kolonu (arşivde yok)
    "ilce": "ilce",
    "proje yeri": "proje_yeri",
    "karar sonucu": "sonuc",
}


def _norm(s: object) -> str:
    """Türkçe harfleri ASCII'ye çevir, boşlukları sadeleştir, küçült (başlık eşlemesi)."""
    table = str.maketrans("İıŞşĞğÇçÖöÜü", "iissggccoouu")
    return " ".join(str(s).translate(table).split()).lower()


def _temiz(value: object) -> str | None:
    """Hücre → düz metin (boş / "-" / "—" placeholder → None)."""
    if value is None:
        return None
    text = str(value).strip()
    return text if text and text not in {"-", "—", "–"} else None


def _to_date(value: object) -> date | None:
    """Hücre → date (datetime ise .date(); 'gg.aa.yyyy' / 'gg/aa/yyyy' metni ayrıştırılır)."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _temiz(value)
    if not text:
        return None
    for sep in (".", "/", "-"):
        parcalar = text.split(sep)
        if len(parcalar) != 3:
            continue
        try:
            a, b, c = (int(p) for p in parcalar)
        except ValueError:
            continue
        # "gg.aa.yyyy" varsayılan; ilk parça 4 haneli ise ISO "yyyy-aa-gg".
        d, m, y = (c, b, a) if a >= 1000 else (a, b, c)
        if not 1900 <= y <= 2100:  # 2 haneli yıl / bozuk hücre → güvenme
            continue
        try:
            return date(y, m, d)
        except ValueError:
            continue
    return None


@dataclass(frozen=True)
class CedKarar:
    """Tek ÇED kararı (XLSX satırından)."""

    karar_no: str | None = None
    tarih: date | None = None
    sektor: str | None = None
    proje_adi: str | None = None
    proje_sahibi: str | None = None
    il: str | None = None
    ilce: str | None = None  # ayrı İLÇESİ kolonu (eced 2025+); arşivde PROJE YERİ içinde
    proje_yeri: str | None = None
    sonuc: str | None = None

    @property
    def olumlu(self) -> bool:
        return bool(self.sonuc) and "olumlu" in _norm(self.sonuc)

    @property
    def il_tokenlar(self) -> list[str]:
        """İl hücresi tek il ya da virgülle ayrılmış çok-il listesi olabilir → token'lar."""
        if not self.il:
            return []
        return [t.strip() for t in self.il.split(",") if t.strip()]

    def il_eslesir(self, hedef_norm: str) -> bool:
        """Hedef il (normalize) bu kararın il token'larından biri mi?"""
        return any(_norm(t) == hedef_norm for t in self.il_tokenlar)

    @property
    def kayit_anahtari(self) -> str:
        """Satır kimliği: karar no varsa onu, yoksa içerik hash'i (KARAR NO sık boş)."""
        if self.karar_no:
            return f"no={self.karar_no}"
        blob = "|".join(
            _norm(x)
            for x in (
                self.tarih.isoformat() if self.tarih else "",
                self.il,
                self.proje_yeri,
                self.proje_adi,
                self.sonuc,
            )
        )
        return "h=" + hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]

    @property
    def ozet(self) -> str:
        """Sektör — proje adı (proje sahibi) [sonuç]."""
        parcalar = [p for p in (self.sektor, self.proje_adi) if p]
        ozet = " — ".join(parcalar) or (self.proje_adi or "ÇED kararı")
        if self.proje_sahibi:
            ozet += f" ({self.proje_sahibi})"
        if self.proje_yeri:  # ham lokatör korunur (sonradan re-geocode için)
            ozet += f" @ {self.proje_yeri}"
        if self.sonuc:
            ozet += f" [{self.sonuc}]"
        return ozet

    @property
    def asama(self) -> str:
        """ÇED olumlu = proje resmen onaylı (karar). Olumsuz/iptal/iade ham saklanır."""
        return "karar" if self.olumlu else (_norm(self.sonuc)[:16] if self.sonuc else "karar")

    @property
    def kesinlik(self) -> float:
        """İl + proje-yeri → 0.4 ; yalnız il → 0.3 ; hiçbiri → 0.15 (R.Gazete ile aynı)."""
        if self.il and self.proje_yeri:
            return 0.4
        if self.il:
            return 0.3
        return 0.15


def _kolon_indeksleri(rows: list[Sequence[object]]) -> tuple[int, dict[str, int]]:
    """Başlık satırını bul (``KARAR NO`` + ``İLİ``) → alan → kolon indeks haritası döndür."""
    for i, row in enumerate(rows):
        eslesme: dict[str, int] = {}
        for j, hucre in enumerate(row):
            alan = _KOLON_ESLEME.get(_norm(hucre))
            if alan and alan not in eslesme:
                eslesme[alan] = j
        # Arşiv "KARAR NO"lu, eced 2025+ değil → İLİ + KARAR SONUCU ortak çapa.
        if "il" in eslesme and "sonuc" in eslesme:
            return i, eslesme
    raise ValueError("The EIA workbook needs a header row with İLİ and KARAR SONUCU columns.")


def parse_ced_rows(rows: Iterable[Sequence[object]]) -> list[CedKarar]:
    """Satır dizilerini ``CedKarar`` listesine çevir (saf — IO yok, kolon sırası bağımsız)."""
    satirlar = list(rows)
    baslik_i, idx = _kolon_indeksleri(satirlar)

    def al(row: Sequence[object], alan: str) -> object:
        j = idx.get(alan)
        return row[j] if j is not None and j < len(row) else None

    kararlar: list[CedKarar] = []
    for row in satirlar[baslik_i + 1 :]:
        karar_no = _temiz(al(row, "karar_no"))
        il = _temiz(al(row, "il"))
        if not karar_no and not il:  # tamamen boş / dipnot satırı
            continue
        kararlar.append(
            CedKarar(
                karar_no=karar_no,
                tarih=_to_date(al(row, "tarih")),
                sektor=_temiz(al(row, "sektor")),
                proje_adi=_temiz(al(row, "proje_adi")),
                proje_sahibi=_temiz(al(row, "proje_sahibi")),
                il=il,
                ilce=_temiz(al(row, "ilce")),
                proje_yeri=_temiz(al(row, "proje_yeri")),
                sonuc=_temiz(al(row, "sonuc")),
            )
        )
    return kararlar


def parse_ced_xlsx(content: bytes) -> list[CedKarar]:
    """XLSX byte içeriği → ``CedKarar`` listesi (ilk sheet).

    ``read_only=False``: eced 2025+ dosyasında dimension metadata bozuk olduğundan
    read-only akış tek satır döndürüyor; tam yükleme güvenilir (dosyalar ≤2 MB).
    """
    with warnings.catch_warnings():  # eced dosyası "no default style" uyarısı verir (zararsız)
        warnings.simplefilter("ignore")
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=False, data_only=True)
    ws = wb[wb.sheetnames[0]]
    rows = [tuple(r) for r in ws.iter_rows(values_only=True)]
    wb.close()
    return parse_ced_rows(rows)


@dataclass
class CedRaporu:
    """ÇED yükleme özeti."""

    kaynak_url: str
    toplam_karar: int = 0
    filtre_sonrasi: int = 0
    il_bulunan: int = 0
    yer_bulunan: int = 0
    geocoded: int = 0
    geocode_durdu: bool = False  # TKGM WAF → kalan olaylar geom'suz kaydedildi
    yazilan: int = 0
    atlanan: int = 0


class CedClient:
    """ÇED XLSX indirici (nazik HTTP; csb.gov.tr yerel DNS'te sorunsuz)."""

    def __init__(self, client: PoliteClient | None = None) -> None:
        self._owns = client is None
        self._client = client or PoliteClient(dns_overrides={})

    def indir(self, url: str) -> bytes:
        r = self._client.get(url)
        r.raise_for_status()
        return r.content

    def close(self) -> None:
        if self._owns:
            self._client.close()

    def __enter__(self) -> CedClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


def yukle(
    url: str = CED_OLUMLU_ARSIV_URL,
    client: CedClient | None = None,
    il_filtre: str | None = None,
    yil_min: int | None = None,
    yalniz_olumlu: bool = True,
    kaydet: bool = True,
    geocode: bool = False,
    geocoder: TkgmGeocoder | None = None,
) -> CedRaporu:
    """ÇED XLSX'i indir → filtrele (il / yıl / sonuç) → ``katalizor_olay``'a yaz.

    Yazılan her olay: ``tur="ced"``, il/proje-yeri granül, ``valid_time``=KARAR TARİHİ.
    ``dedup_anahtar=ced:{kayit_anahtari}`` ile idempotent (kaynak URL değişse bile).
    ``geocode=True`` ise PROJE YERİ'nden mahalle/ada/parsel çözülüp TKGM ile gerçek
    geometri eklenir (kesinlik ``GEOCODE_KESINLIK``'e yükselir).
    """
    rapor = CedRaporu(kaynak_url=url)
    cli = client or CedClient()
    il_hedef = _norm(il_filtre) if il_filtre else None
    gc = geocoder
    gc_owns = geocode and gc is None
    if gc_owns:
        gc = TkgmGeocoder()
    gc_durdu = False  # TKGM rate-limit'inde geocode'u kapat (olaylar geom'suz kaydedilir)
    try:
        content = cli.indir(url)
        ham_yol = str(save_raw(content, source="ced", name=url.rsplit("/", 1)[-1]))
        kararlar = parse_ced_xlsx(content)
        rapor.toplam_karar = len(kararlar)

        for k in kararlar:
            if yalniz_olumlu and not k.olumlu:
                continue
            if il_hedef and not k.il_eslesir(il_hedef):
                continue
            if yil_min and (k.tarih is None or k.tarih.year < yil_min):
                continue
            rapor.filtre_sonrasi += 1
            # Çok-il listesinde eşleşen il'i (yoksa birincil token'ı) sakla.
            il_kayit = next(
                (t for t in k.il_tokenlar if il_hedef and _norm(t) == il_hedef),
                k.il_tokenlar[0] if k.il_tokenlar else None,
            )
            if il_kayit:
                rapor.il_bulunan += 1
            if k.proje_yeri:
                rapor.yer_bulunan += 1

            lok = parse_yer(k.proje_yeri)
            if k.ilce and not lok.ilce:  # eced 2025+ ayrı İLÇESİ kolonu → geocode'a besle
                lok = replace(lok, ilce=k.ilce)
            ilce_kayit = k.ilce or lok.ilce or (k.proje_yeri[:64] if k.proje_yeri else None)
            geom = None
            kesinlik = k.kesinlik
            if geocode and gc is not None and not gc_durdu and lok.geocode_edilebilir:
                try:
                    sonuc = gc.coz(il_kayit, lok)
                except TkgmRateLimitError:
                    gc_durdu = True  # WAF → bundan sonrası geom'suz; çekim sürsün
                    rapor.geocode_durdu = True
                    sonuc = None
                geojson = temsili_geometri(sonuc) if sonuc is not None else None
                geom = geojson_to_multipolygon(geojson) if geojson else None
                if geom is not None and sonuc is not None:
                    kesinlik = GEOCODE_KESINLIK
                    rapor.geocoded += 1
                    # TKGM otantik il/ilçe adı (ilçe-siz fallback'te dağınık metni temizler).
                    ilce_kayit = sonuc.ilce or ilce_kayit
                    il_kayit = sonuc.il or il_kayit

            if kaydet:
                valid_time = (
                    datetime(k.tarih.year, k.tarih.month, k.tarih.day, tzinfo=UTC)
                    if k.tarih
                    else None
                )
                _, eklendi = kaydet_katalizor_olay(
                    tur="ced",
                    kaynak="ced.csb",
                    ozet=k.ozet,
                    il=il_kayit,
                    ilce=ilce_kayit,
                    asama=k.asama,
                    kesinlik=kesinlik,
                    geom=geom,
                    kaynak_url=f"{url}#{k.kayit_anahtari}",
                    raw_ref=ham_yol,
                    valid_time=valid_time,
                    # URL'den bağımsız doğal anahtar: CSB dosyayı yeni URL ile yeniden
                    # yayınlarsa (upload timestamp'li adlar) mükerrer import olmasın.
                    dedup_anahtar=f"ced:{k.kayit_anahtari}",
                )
                if eklendi:
                    rapor.yazilan += 1
                else:
                    rapor.atlanan += 1
    finally:
        if client is None:
            cli.close()
        if gc_owns and gc is not None:
            gc.close()
    return rapor
