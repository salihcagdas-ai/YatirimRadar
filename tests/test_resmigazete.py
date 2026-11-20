"""Resmî Gazete ayrıştırıcısını sentetik bir fihristle sınar (ağsız/DB'siz)."""

from __future__ import annotations

from datetime import date

from yatirimradar.ingestion.resmigazete import (
    KamulastirmaKalem,
    fihrist_url,
    is_acele_kamulastirma,
    parse_baslik,
    parse_fihrist,
)

T_IL = "Örnekkent İli Sınırları İçinde Acele Kamulaştırılması Hakkında Karar (Karar Sayısı: 801)"
T_ILCE = "Deneme İli, Örnek İlçesinde Acele Kamulaştırılması Hakkında Karar (Karar Sayısı: 802)"
T_ILCELER = (
    "Kurgukent İli, Kuzey ve Güney İlçeleri Sınırlarında Acele Kamulaştırılması "
    "Hakkında Karar (Karar Sayısı: 803)"
)
T_ORTA = (
    "Örnek Yol İçin Deneme İli, Batı İlçesinde Acele Kamulaştırılması Hakkında Karar "
    "(Karar Sayısı: 804)"
)
T_HAT = "Enerji İletim Hattı İçin Acele Kamulaştırılması Hakkında Karar (Karar Sayısı: 805)"


def test_fihrist_url() -> None:
    assert fihrist_url(date(2030, 5, 6)) == (
        "https://www.resmigazete.gov.tr/eskiler/2030/05/20300506.htm"
    )
    # tek haneli ay/gün sıfırla doldurulur
    assert fihrist_url(date(2026, 1, 5)).endswith("/2026/01/20260105.htm")


def test_is_acele_kamulastirma() -> None:
    assert is_acele_kamulastirma(T_IL)
    assert is_acele_kamulastirma("ACELE KAMULAŞTIRILMASI HAKKINDA KARAR")
    assert not is_acele_kamulastirma("Örnek bir idari duyuru")


def test_parse_baslik_il_ilce_karar() -> None:
    # tek il, ilçe yok
    assert parse_baslik(T_IL) == ("Örnekkent", None, "801")
    # il + tek ilçe
    assert parse_baslik(T_ILCE) == ("Deneme", "Örnek", "802")
    # il + çok ilçe (ve ile ayrılmış)
    assert parse_baslik(T_ILCELER) == ("Kurgukent", "Kuzey, Güney", "803")
    # il cümle ortasında
    assert parse_baslik(T_ORTA) == ("Deneme", "Batı", "804")
    # ENH/çok-il: il yok ("İletim" yanlış eşleşmemeli)
    il, ilce, karar_no = parse_baslik(T_HAT)
    assert il is None
    assert ilce is None
    assert karar_no == "805"


def test_kesinlik_kademesi() -> None:
    d = date(2030, 5, 6)
    assert KamulastirmaKalem(d, "x", "b", il="Deneme", ilce="Örnek").kesinlik == 0.4
    assert KamulastirmaKalem(d, "x", "b", il="Örnekkent").kesinlik == 0.3
    assert KamulastirmaKalem(d, "x", "b").kesinlik == 0.15


def test_parse_fihrist_sentetik_html() -> None:
    basliklar = (T_IL, T_ILCE, T_ILCELER, T_ORTA, T_HAT)
    linkler = [f'<a href="ek-{i}.pdf">–– {t}</a>' for i, t in enumerate(basliklar)]
    linkler += [linkler[0], '<a href="duyuru.pdf">Örnek duyuru</a>']
    html = '<html><head><meta charset="windows-1254"></head><body>' + "".join(linkler)
    tarih = date(2030, 5, 6)
    kalemler = parse_fihrist(html.encode("cp1254"), "https://arsiv.example/2030/fihrist.htm", tarih)
    assert len(kalemler) == len(basliklar)
    assert [k.baslik for k in kalemler] == list(basliklar)
    for i, k in enumerate(kalemler):
        assert k.pdf_url == f"https://arsiv.example/2030/ek-{i}.pdf"
        assert k.tarih == tarih
    assert (kalemler[1].il, kalemler[1].ilce) == ("Deneme", "Örnek")
    assert kalemler[2].ilce == "Kuzey, Güney"
    assert kalemler[-1].il is None and kalemler[-1].kesinlik == 0.15
