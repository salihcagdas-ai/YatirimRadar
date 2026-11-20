"""Command-line interface for importing, geocoding and ranking land-use decisions."""

from __future__ import annotations

import contextlib
import sys
from datetime import date, datetime, timedelta

import typer

from yatirimradar import __version__
from yatirimradar.config import PROJECT_ROOT, get_settings

# Use UTF-8 when printing Turkish place names on Windows.
with contextlib.suppress(AttributeError, ValueError):
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

_EVENT_TYPES = {
    "kamulastirma": "Expropriation",
    "ced": "EIA",
    "kgm_yol": "Road",
    "osb": "Industrial zone",
}

app = typer.Typer(
    add_completion=False,
    help="Track environmental decisions, expropriations and land assessments in Türkiye.",
)


@app.command()
def version() -> None:
    """Print the installed version."""
    typer.echo(f"YatırımRadar {__version__}")


@app.command("config-check")
def config_check() -> None:
    """Show the active settings with credentials omitted."""
    s = get_settings()
    has_key = "configured" if s.anthropic_api_key else "unset"
    typer.echo(f"Pilot area: {s.pilot_il} / {s.pilot_ilce}")
    typer.echo(f"Database: {s.database_url.split('@')[-1]}")
    typer.echo(f"Anthropic API key: {has_key}")
    typer.echo(f"Geocoding confidence threshold: {s.geocode_confidence_threshold}")
    typer.echo(f"Request interval: {s.request_delay_seconds}s")


@app.command("tkgm-ornek", hidden=True)
@app.command("sample-parcels")
def tkgm_ornek(
    mahalle: str = typer.Option(
        ..., "--neighborhood", "--mahalle",
        help="Neighborhood in the pilot district.",
    ),
    ada_max: int = typer.Option(
        12, "--max-block", "--ada-max",
        help="Highest cadastral block number to check.",
    ),
    parsel_max: int = typer.Option(
        5, "--max-parcel", "--parsel-max",
        help="Highest parcel number to check in each block.",
    ),
    delay: float = typer.Option(1.0, help="Seconds between requests."),
    kaydet: bool = typer.Option(
        True, "--save/--no-save", "--kaydet/--no-kaydet",
        help="Save results to the database.",
    ),
) -> None:
    """Sample block and parcel numbers in a neighborhood and report TKGM's match rate."""
    from yatirimradar.ingestion.http import PoliteClient
    from yatirimradar.ingestion.tkgm import TkgmClient
    from yatirimradar.ingestion.tkgm_ornek import ornekle_mahalle

    s = get_settings()
    client = TkgmClient(PoliteClient(delay_seconds=delay))
    try:
        il_id = client.find_id(client.il_liste(), s.pilot_il)
        if il_id is None:
            typer.echo(f"Province not found: {s.pilot_il}")
            raise typer.Exit(1)
        ilce_id = client.find_id(client.ilce_liste(il_id), s.pilot_ilce)
        if ilce_id is None:
            typer.echo(f"District not found: {s.pilot_ilce}")
            raise typer.Exit(1)
        mid = client.find_id(client.mahalle_liste(ilce_id), mahalle)
        if mid is None:
            typer.echo(f"Neighborhood not found: {mahalle}")
            raise typer.Exit(1)

        typer.echo(f"Scanning: {s.pilot_il}/{s.pilot_ilce}/{mahalle} (neighborhood ID={mid})")
        r = ornekle_mahalle(client, mid, mahalle, ada_max, parsel_max, kaydet)
    finally:
        client.close()

    typer.echo("")
    typer.echo(f"Block/parcel pairs tested: {r.denenen}  ({r.sure_s}s)")
    typer.echo(f"Not found (404): {r.bulunamadi}")
    typer.echo(f"Parcels found: {r.var_olan}")
    typer.echo(f"  Active with geometry: {r.aktif_geom}")
    typer.echo(f"  With geometry from parcel history: {r.soyagaci_geom}")
    typer.echo(f"  Inactive without geometry: {r.pasif_geomsuz}")
    typer.echo(f"Errors: {r.hata}")
    typer.echo(
        f"Saved: {r.kaydedilen_parsel} parcels, "
        f"{r.kaydedilen_soyagaci} parcel history records"
    )
    typer.echo(f"Match rate: {r.cozum_orani:.0%} (target: at least 80%)")


def _province_filter(value: str, default: str | None = None) -> str | None:
    return None if value.casefold() in {"all", "tum"} else value or default


def _tarih_parse(deger: str) -> date:
    try:
        return datetime.strptime(deger, "%Y-%m-%d").date()
    except ValueError as exc:
        raise typer.BadParameter("Use the YYYY-MM-DD date format.") from exc


@app.command("resmigazete-tara", hidden=True)
@app.command("scan-gazette")
def resmigazete_tara(
    tarih: str = typer.Option(
        ..., "--date", "--tarih",
        help="Start date (YYYY-MM-DD).",
    ),
    bitis: str = typer.Option(
        "", "--end-date", "--bitis",
        help="End date (YYYY-MM-DD). Defaults to the start date.",
    ),
    indir: bool = typer.Option(
        True, "--download/--no-download", "--indir/--no-indir",
        help="Download decision PDFs to data/raw.",
    ),
    kaydet: bool = typer.Option(
        True, "--save/--no-save", "--kaydet/--no-kaydet",
        help="Save events to the database.",
    ),
    delay: float = typer.Option(2.0, help="Seconds between requests."),
) -> None:
    """Scan the Official Gazette for urgent expropriation decisions."""
    from yatirimradar.ingestion.http import PoliteClient
    from yatirimradar.ingestion.resmigazete import ResmiGazeteClient, tara_gun

    bas = _tarih_parse(tarih)
    son = _tarih_parse(bitis) if bitis else bas
    if son < bas:
        raise typer.BadParameter("The end date must be on or after the start date.")

    rg = ResmiGazeteClient(PoliteClient(dns_overrides={}, delay_seconds=delay))
    toplam_kalem = toplam_yazilan = toplam_pdf = 0
    try:
        gun = bas
        while gun <= son:
            try:
                r = tara_gun(gun, client=rg, indir_pdf=indir, kaydet=kaydet)
            except Exception as exc:  # noqa: BLE001 - continue with the next day
                typer.echo(f"{gun}  Error: {exc}")
                gun += timedelta(days=1)
                continue
            toplam_kalem += r.fihrist_kalem
            toplam_yazilan += r.yazilan
            toplam_pdf += r.indirilen_pdf
            typer.echo(
                f"{gun}  entries={r.fihrist_kalem}  provinces={r.il_bulunan}  "
                f"districts={r.ilce_bulunan}  "
                f"pdf={r.indirilen_pdf}  saved={r.yazilan}  skipped={r.atlanan}  errors={r.hata}"
            )
            for k in r.kalemler:
                yer = "/".join(p for p in (k.il, k.ilce) if p) or "(province unknown)"
                typer.echo(f"    [{k.karar_no or '?'}] {yer} — {k.baslik[:90]}")
            gun += timedelta(days=1)
    finally:
        rg.close()

    typer.echo("")
    typer.echo(
        f"Total: entries={toplam_kalem}  saved={toplam_yazilan}  PDFs downloaded={toplam_pdf}"
    )


@app.command("ced-yukle", hidden=True)
@app.command("import-eia")
def ced_yukle(
    url: str = typer.Option("", help="Override the source workbook URL."),
    guncel: bool = typer.Option(
        False, "--current/--no-current", "--guncel/--no-guncel",
        help="Use the current EIA workbook (2025 onward).",
    ),
    il: str = typer.Option(
        "", "--province", "--il",
        help="Province to include. Defaults to the pilot province. Use 'all' for every province.",
    ),
    yil_min: int = typer.Option(
        0, "--min-year", "--yil-min",
        help="Earliest decision year to include. Zero includes all years.",
    ),
    yalniz_olumlu: bool = typer.Option(
        True, "--positive-only/--no-positive-only", "--yalniz-olumlu/--no-yalniz-olumlu",
        help="Include only favorable EIA decisions (ÇED Olumlu).",
    ),
    geocode: bool = typer.Option(False, help="Look up the project location in the TKGM cadastre."),
    kaydet: bool = typer.Option(
        True, "--save/--no-save", "--kaydet/--no-kaydet",
        help="Save events to the database.",
    ),
    delay: float = typer.Option(2.0, help="Seconds between requests."),
) -> None:
    """Import environmental impact assessment (EIA) decisions from a ministry workbook."""
    from yatirimradar.geocode.cozucu import TkgmGeocoder
    from yatirimradar.ingestion.ced import (
        CED_OLUMLU_ARSIV_URL,
        CED_OLUMLU_GUNCEL_URL,
        CedClient,
        yukle,
    )
    from yatirimradar.ingestion.http import PoliteClient
    from yatirimradar.ingestion.tkgm import TkgmClient

    s = get_settings()
    il_filtre = _province_filter(il, s.pilot_il)

    kaynak_url = url or (CED_OLUMLU_GUNCEL_URL if guncel else CED_OLUMLU_ARSIV_URL)
    with contextlib.ExitStack() as stack:
        http = stack.enter_context(PoliteClient(dns_overrides={}, delay_seconds=delay))
        gc = None
        if geocode:
            geo_http = stack.enter_context(PoliteClient(delay_seconds=delay))
            gc = TkgmGeocoder(TkgmClient(geo_http))
        r = yukle(
            kaynak_url,
            client=CedClient(http),
            il_filtre=il_filtre,
            yil_min=yil_min or None,
            yalniz_olumlu=yalniz_olumlu,
            kaydet=kaydet,
            geocode=geocode,
            geocoder=gc,
        )

    typer.echo(f"Source: {r.kaynak_url}")
    typer.echo(f"Province: {il_filtre or 'all'}  minimum year={yil_min or '-'}")
    typer.echo(f"Decisions read: {r.toplam_karar}")
    typer.echo(f"Matching decisions: {r.filtre_sonrasi}")
    typer.echo(f"  With a province: {r.il_bulunan}")
    typer.echo(f"  With a project location: {r.yer_bulunan}")
    if geocode:
        typer.echo(f"  With matched geometry: {r.geocoded}")
        if r.geocode_durdu:
            typer.echo("TKGM rate limit reached (403). Resume later with geocode-pending.")
    typer.echo(f"Saved: {r.yazilan} (skipped or already present: {r.atlanan})")


@app.command("rayic-yukle", hidden=True)
@app.command("import-assessments")
def rayic_yukle(
    csv_yol: str | None = typer.Option(
        None, "--csv", help="CSV file. Defaults to all CSVs in the local assessments directory."
    ),
    kaydet: bool = typer.Option(
        True, "--save/--no-save", "--kaydet/--no-kaydet",
        help="Save price records to the database.",
    ),
) -> None:
    """Import assessed land values per square meter from local CSV files."""
    from pathlib import Path

    from yatirimradar.ingestion.rayic import yukle_rayic_dosyalari

    yol = Path(csv_yol) if csv_yol else None
    try:
        r = yukle_rayic_dosyalari(yol, kaydet=kaydet)
    except OSError as exc:
        typer.echo(f"Could not read CSV: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Source: {yol or get_settings().rayic_dizini}")
    if not r.toplam:
        typer.echo("No assessment records found to import.")
    typer.echo(f"Records read: {r.toplam}")
    typer.echo(f"Saved: {r.yazilan} (skipped or already present: {r.atlanan})")


@app.command("skorla", hidden=True)
@app.command("score")
def skorla_cmd(
    il: str = typer.Option(
        "", "--province", "--il",
        help="Province to include. Defaults to the pilot province. Use 'all' for every province.",
    ),
    top: int = typer.Option(15, help="Number of top-ranked events to display."),
    sadece_geomlu: bool = typer.Option(
        False, "--mapped-only/--no-mapped-only", "--sadece-geomlu/--no-sadece-geomlu",
        help="Include only events with mapped geometry.",
    ),
    as_of: str = typer.Option(
        "", help="Use records known at 00:00 UTC on this date (YYYY-MM-DD). Defaults to now."
    ),
) -> None:
    """Rank events using their location, source, timing and available price observations."""
    from datetime import UTC

    from yatirimradar.scoring.sirala import firsatlari_sirala

    s = get_settings()
    il_filtre = _province_filter(il, s.pilot_il)
    asof_dt = (
        datetime.combine(_tarih_parse(as_of), datetime.min.time(), tzinfo=UTC) if as_of else None
    )

    firsatlar = firsatlari_sirala(
        il=il_filtre, as_of=asof_dt, limit=top, sadece_geomlu=sadece_geomlu
    )
    baslik = il_filtre or "all provinces"
    typer.echo(f"Top {len(firsatlar)} events — {baslik}")
    typer.echo(f"{'#':>2}  {'score':>5}  {'type':<14} {'district':<12} {'TRY/m²':>6} map   summary")
    for i, f in enumerate(firsatlar, 1):
        b = f.bilesen
        rayic = f"{int(f.rayic_tl_m2)}" if f.rayic_tl_m2 else "-"
        ilce = (f.ilce or "-")[:12]
        ozet = (f.ozet or "")[:46]
        typer.echo(
            f"{i:>2}  {f.skor:>5.3f}  {_EVENT_TYPES.get(f.tur, f.tur):<14} {ilce:<12} {rayic:>6} "
            f"{'✓' if f.geomlu else ' ':>4}  {ozet}"
        )
        typer.echo(
            f"      (type {b.tur_agirlik:.2f} × confidence {b.kesinlik:.2f} × "
            f"recency {b.tazelik:.2f} × affordability {b.ucuzluk:.2f} × "
            f"density {b.yogunluk:.2f} × scale {b.buyukluk:.2f})"
        )
        if f.fiyat_tipi:
            price_type = {"rayic": "Assessed value", "ilan": "Asking price"}.get(
                f.fiyat_tipi, f.fiyat_tipi
            )
            level = {"mahalle": "neighborhood", "ilce": "district"}.get(
                f.fiyat_duzeyi or "", f.fiyat_duzeyi or "unknown area"
            )
            typer.echo(f"      {price_type} · {level} · {f.fiyat_yili or '-'}")


@app.command("harita-export", hidden=True)
@app.command("export-map")
def harita_export(
    il: str = typer.Option(
        "", "--province", "--il",
        help="Province to include. Defaults to the pilot province. Use 'all' for every province.",
    ),
    cikti: str = typer.Option(
        "data/processed/firsatlar.geojson", "--output", "--cikti",
        help="Output GeoJSON file.",
    ),
    limit: int = typer.Option(500, help="Maximum number of features to export."),
) -> None:
    """Export ranked events and their geometry as GeoJSON."""
    import json
    from pathlib import Path

    from yatirimradar.scoring.sirala import firsatlar_geojson

    s = get_settings()
    il_filtre = _province_filter(il, s.pilot_il)
    fc = firsatlar_geojson(il=il_filtre, limit=limit)
    govde = json.dumps(fc, ensure_ascii=False)
    yol = Path(cikti)
    yol.parent.mkdir(parents=True, exist_ok=True)
    yol.write_text(govde, encoding="utf-8")
    # Keep a copy alongside the map for static previews.
    onizleme = PROJECT_ROOT / "frontend" / "firsatlar.geojson"
    onizleme.write_text(govde, encoding="utf-8")
    typer.echo(f"Exported {len(fc['features'])} features to {yol} and {onizleme}")


@app.command("rg-parsel-coz", hidden=True)
@app.command("resolve-gazette-parcels")
def rg_parsel_coz(
    kaydet: bool = typer.Option(
        True, "--save/--no-save", "--kaydet/--no-kaydet",
        help="Save matched geometry to the event table.",
    ),
    delay: float = typer.Option(0.6, help="Seconds between TKGM requests."),
) -> None:
    """Match parcel references from local expropriation decisions to TKGM geometry."""
    from yatirimradar.extraction.kamulastirma import seed_yukle, uygula
    from yatirimradar.geocode.cozucu import TkgmGeocoder
    from yatirimradar.ingestion.http import PoliteClient
    from yatirimradar.ingestion.tkgm import TkgmClient

    kayitlar = seed_yukle()
    if not kayitlar:
        typer.echo("No decisions found in the local expropriation parcel file.")
        return
    gc = TkgmGeocoder(TkgmClient(PoliteClient(delay_seconds=delay)))
    try:
        r = uygula(kayitlar, gc, kaydet=kaydet)
    finally:
        gc.close()
    typer.echo(f"Local decisions: {r.karar_sayisi}")
    typer.echo(f"Parcels matched: {r.parsel_cozulen}")
    typer.echo(f"Events matched: {r.olay_eslesen}")
    typer.echo(f"Geometries saved: {r.geom_yazilan}")
    if r.eslenmeyen:
        typer.echo(f"Unmatched decisions: {', '.join(r.eslenmeyen)} (run scan-gazette first)")
    if r.rate_limit:
        typer.echo(f"TKGM rejected the request. Batch stopped. Response: {r.mesaj or '403'}")
        typer.echo("If the daily quota has been reached, resume after it resets.")


@app.command("pipeline")
def pipeline(
    il: str = typer.Option(
        "", "--province", "--il",
        help="Province to include. Defaults to the pilot province. Use 'all' for every province.",
    ),
    cek: bool = typer.Option(
        True, "--fetch/--no-fetch", "--cek/--no-cek",
        help="Fetch and geocode EIA decisions before ranking.",
    ),
    guncel: bool = typer.Option(
        True, "--current/--no-current", "--guncel/--no-guncel",
        help="Include the current EIA workbook (2025 onward) when fetching.",
    ),
    rayic: bool = typer.Option(
        True, "--assessments/--no-assessments", "--rayic/--no-rayic",
        help="Import missing rows from the local assessments directory.",
    ),
    top: int = typer.Option(10, help="Number of ranked events to display and log."),
    delay: float = typer.Option(0.6, help="Seconds between requests."),
) -> None:
    """Import data, rank events, export GeoJSON and save a dated ranking."""
    import json

    from yatirimradar.geocode.cozucu import TkgmGeocoder
    from yatirimradar.ingestion.ced import (
        CED_OLUMLU_ARSIV_URL,
        CED_OLUMLU_GUNCEL_URL,
        CedClient,
        yukle,
    )
    from yatirimradar.ingestion.http import PoliteClient
    from yatirimradar.ingestion.rayic import yukle_rayic_dosyalari
    from yatirimradar.ingestion.tkgm import TkgmClient
    from yatirimradar.scoring.sirala import firsatlar_geojson, firsatlari_sirala

    s = get_settings()
    il_filtre = _province_filter(il, s.pilot_il)

    if cek:
        with (
            PoliteClient(delay_seconds=delay) as geo_http,
            PoliteClient(dns_overrides={}, delay_seconds=delay) as ced_http,
        ):
            gc = TkgmGeocoder(TkgmClient(geo_http))
            kaynaklar = [(CED_OLUMLU_ARSIV_URL, "archive")]
            if guncel:
                kaynaklar.append((CED_OLUMLU_GUNCEL_URL, "current"))
            cli = CedClient(ced_http)
            for url, tur in kaynaklar:
                r = yukle(url, client=cli, il_filtre=il_filtre, geocode=True, geocoder=gc)
                typer.echo(
                    f"EIA {tur}: {r.filtre_sonrasi} events, "
                    f"{r.geocoded} geocoded ({r.yazilan} new, {r.atlanan} skipped)"
                    + ("  [TKGM quota reached]" if r.geocode_durdu else "")
                )

    if rayic:
        rr = yukle_rayic_dosyalari()
        typer.echo(f"Assessments: {rr.yazilan} new, {rr.atlanan} already present" if rr.toplam
                   else "No local assessment records found. Ranking will use available prices.")

    fc = firsatlar_geojson(il=il_filtre, limit=500)
    cikti = PROJECT_ROOT / "data" / "processed" / "firsatlar.geojson"
    cikti.parent.mkdir(parents=True, exist_ok=True)
    govde = json.dumps(fc, ensure_ascii=False)
    cikti.write_text(govde, encoding="utf-8")
    (PROJECT_ROOT / "frontend" / "firsatlar.geojson").write_text(govde, encoding="utf-8")
    typer.echo(f"Map: exported {len(fc['features'])} geocoded events to {cikti.name}")

    from datetime import UTC

    from yatirimradar.paper_trading.gunluk import tahminleri_kaydet

    kesim = datetime.now(UTC)
    siralama = firsatlari_sirala(il=il_filtre, limit=top, as_of=kesim)
    yol = tahminleri_kaydet(siralama, kesim, il_filtre, s.data_processed_dir / "tahminler")
    typer.echo(f"Ranking snapshot: {yol.name}")
    typer.echo(f"\nTop {top} events:")
    for i, f in enumerate(siralama, 1):
        typer.echo(
            f"{i:>2}  {f.skor:.3f}  {_EVENT_TYPES.get(f.tur, f.tur):<14} {(f.ilce or '-'):<12} "
            f"{'geom' if f.geomlu else '    '}  {(f.ozet or '')[:48]}"
        )
    typer.echo("\nOpen the map with uv run yatirimradar serve")


@app.command("backtest")
def backtest_cmd(
    il: str = typer.Option(
        "", "--province", "--il",
        help="Province to include. Defaults to the pilot province. Use 'all' for every province.",
    ),
    esik: float = typer.Option(
        0.4, "--threshold", "--esik",
        help="Minimum score for an event to be counted as an open position.",
    ),
    as_of: str = typer.Option(
        "", help="Use records known at 00:00 UTC on this date (YYYY-MM-DD). Defaults to now."
    ),
) -> None:
    """Report open positions and counts by decision year using the selected knowledge date."""
    from datetime import UTC

    from yatirimradar.paper_trading.backtest import (
        acik_pozisyonlar,
        knowledge_zaman_araligi,
        pozisyon_takvimi,
    )

    s = get_settings()
    il_filtre = _province_filter(il, s.pilot_il)
    asof_dt = (
        datetime.combine(_tarih_parse(as_of), datetime.min.time(), tzinfo=UTC) if as_of else None
    )

    pozisyonlar = acik_pozisyonlar(as_of=asof_dt, il=il_filtre, esik=esik)
    typer.echo(
        f"Open positions (score ≥ {esik}, province: {il_filtre or 'all'}): {len(pozisyonlar)}"
    )

    typer.echo("\nEvents by decision year:")
    typer.echo(f"  {'year':<6}{'new':>6}{'cumulative':>11}{'avg.score':>10}")
    for t in pozisyon_takvimi(pozisyonlar):
        typer.echo(f"  {t.yil:<6}{t.yeni:>6}{t.kumulatif:>11}{t.ort_skor:>10.3f}")

    kmin, kmax = knowledge_zaman_araligi()
    if kmin and kmax and (kmax - kmin).days < 1:
        typer.echo(
            "\nThe records were ingested within a single day. Historical replay becomes more "
            "informative as new records arrive. The table groups events by decision year."
        )


@app.command("h3-yogunluk", hidden=True)
@app.command("h3-density")
def h3_yogunluk(
    il: str = typer.Option(
        "", "--province", "--il",
        help="Province to include. Defaults to the pilot province. Use 'all' for every province.",
    ),
    cozunurluk: int = typer.Option(
        7, "--resolution", "--cozunurluk",
        help="H3 grid resolution.",
    ),
    top: int = typer.Option(10, help="Number of cells to display."),
) -> None:
    """Count geocoded events in H3 cells and show the busiest cells."""
    from yatirimradar.features.h3_ozellik import katalizor_yogunlugu

    s = get_settings()
    il_filtre = _province_filter(il, s.pilot_il)
    hucreler = katalizor_yogunlugu(il=il_filtre, cozunurluk=cozunurluk)
    typer.echo(
        f"H3 resolution {cozunurluk}, province: {il_filtre or 'all'}, cells: {len(hucreler)}"
    )
    typer.echo(f"  {'count':>5}  {'lat':>8} {'lon':>8}  cell")
    for h in hucreler[:top]:
        typer.echo(f"  {h.sayi:>5}  {h.lat:>8.4f} {h.lon:>8.4f}  {h.hucre}")


@app.command("durum", hidden=True)
@app.command("status")
def durum_cmd() -> None:
    """Show event, parcel and price coverage in the local database."""
    from yatirimradar.durum import durum_topla

    d = durum_topla()
    typer.echo("YatırımRadar data coverage")
    typer.echo(f"Events: {d.katalizor_toplam}")
    for tur, sayi in sorted(d.tur_dagilimi.items()):
        typer.echo(f"  - {_EVENT_TYPES.get(tur, tur):<14}: {sayi}")
    typer.echo(f"Mapped events: {d.geom_var} ({d.geocode_orani:.1%})")
    typer.echo(f"Pending geocoding: {d.geocode_bekleyen} (block and parcel references available)")
    typer.echo(f"Events without geometry: {d.geom_yok}")
    typer.echo(f"Price records: {d.fiyat_toplam}")
    typer.echo(f"Cadastral parcels: {d.parsel_toplam}")
    if d.valid_min and d.valid_max:
        typer.echo(f"Decision date range: {d.valid_min:%Y-%m-%d} → {d.valid_max:%Y-%m-%d}")
    if d.son_cekim:
        typer.echo(f"Last import: {d.son_cekim:%Y-%m-%d %H:%M}")


@app.command("geocode-arkaplan", hidden=True)
@app.command("geocode-pending")
def geocode_arkaplan_cmd(
    adet: int = typer.Option(
        200, "--limit", "--adet",
        help="Maximum TKGM geocoding attempts in this batch.",
    ),
    delay: float = typer.Option(1.0, help="Seconds between TKGM requests."),
    il: str = typer.Option(
        "", "--province", "--il",
        help="Province to include. Defaults to all provinces.",
    ),
    tumunu_dene: bool = typer.Option(
        False, "--retry-unresolved", "--tumunu-dene", help="Retry previously unresolved locations."
    ),
) -> None:
    """Geocode a batch of unmapped EIA events using TKGM."""
    from yatirimradar.geocode.arkaplan import geocode_partisi

    r = geocode_partisi(
        adet=adet, delay=delay, il=_province_filter(il), denenen_atla=not tumunu_dene
    )
    typer.echo(
        f"Reviewed {r.bakilan} · attempted {r.geocode_denenen} · matched {r.cozulen} · "
        f"skipped {r.atlanan} (missing block or parcel reference)"
    )
    if r.erken_durdu:
        typer.echo(f"TKGM rejected the request. Batch stopped. Response: {r.mesaj or '403'}")
        typer.echo("If the daily quota has been reached, resume after it resets.")
    typer.echo("Check progress with uv run yatirimradar status")


@app.command("gunluk", hidden=True)
@app.command("daily")
def gunluk(
    gun_geri: int = typer.Option(
        3, "--days", "--gun-geri",
        help="Number of days of Official Gazette decisions to scan, including today.",
    ),
    geocode_adet: int = typer.Option(
        150, "--geocode-limit", "--geocode-adet",
        help="Maximum events to geocode in this run.",
    ),
    ced_guncelle: bool = typer.Option(
        False, "--refresh-eia/--no-refresh-eia", "--ced-guncelle/--no-ced-guncelle",
        help="Refresh the current EIA workbook.",
    ),
    delay: float = typer.Option(0.6, help="Seconds between requests."),
) -> None:
    """Refresh decisions, geocode pending events, update the map and save a ranking snapshot.

    Run manually or from a scheduler. Later steps continue if an earlier step fails.
    """
    import json
    from collections.abc import Iterator
    from datetime import UTC
    from zoneinfo import ZoneInfo

    bugun = datetime.now(ZoneInfo("Europe/Istanbul")).date()
    hatalar: list[str] = []

    @contextlib.contextmanager
    def adim(ad: str) -> Iterator[None]:
        try:
            yield
        except Exception as exc:
            hatalar.append(ad)
            typer.echo(f"[daily] {ad} error: {exc}", err=True)

    typer.echo(f"[daily] Starting daily refresh for {bugun}")
    with adim("Official Gazette"):
        from yatirimradar.ingestion.http import PoliteClient
        from yatirimradar.ingestion.resmigazete import ResmiGazeteClient, tara_gun

        yeni = 0
        with PoliteClient(dns_overrides={}, delay_seconds=delay) as http:
            rg = ResmiGazeteClient(http)
            for i in range(gun_geri):
                g = bugun - timedelta(days=i)
                with adim(f"Official Gazette {g}"):
                    yeni += tara_gun(g, client=rg, indir_pdf=False).yazilan
        typer.echo(f"[daily] Official Gazette: {yeni} new expropriation events")

    if ced_guncelle:
        with adim("EIA"):
            from yatirimradar.ingestion.ced import CED_OLUMLU_GUNCEL_URL, CedClient, yukle
            from yatirimradar.ingestion.http import PoliteClient

            with PoliteClient(dns_overrides={}, delay_seconds=delay) as http:
                r = yukle(CED_OLUMLU_GUNCEL_URL, client=CedClient(http), il_filtre=None)
            typer.echo(f"[daily] Current EIA decisions: {r.yazilan} new events")

    with adim("geocode"):
        from yatirimradar.geocode.arkaplan import geocode_partisi

        gr = geocode_partisi(adet=geocode_adet, delay=delay)
        typer.echo(f"[daily] Geocoding: {gr.cozulen} matched"
                   + ("  (TKGM quota reached, resume later)" if gr.erken_durdu else ""))

    with adim("assessments"):
        from yatirimradar.ingestion.rayic import yukle_rayic_dosyalari

        rr = yukle_rayic_dosyalari()
        typer.echo(
            f"[daily] Assessments: {rr.yazilan} new, {rr.atlanan} already present"
            if rr.toplam else "[daily] No local assessment records found."
        )

    with adim("map"):
        from yatirimradar.scoring.sirala import firsatlar_geojson

        fc = firsatlar_geojson(il=None, limit=2000)
        govde = json.dumps(fc, ensure_ascii=False)
        (PROJECT_ROOT / "data" / "processed" / "firsatlar.geojson").write_text(govde, "utf-8")
        (PROJECT_ROOT / "frontend" / "firsatlar.geojson").write_text(govde, "utf-8")
        typer.echo(f"[daily] Map: {len(fc['features'])} events")

    with adim("status"):
        from yatirimradar.durum import durum_topla

        d = durum_topla()
        typer.echo(
            f"[daily] Coverage: {d.katalizor_toplam} events · {d.geom_var} mapped · "
            f"{d.geocode_bekleyen} pending geocoding"
        )
    with adim("ranking snapshot"):
        from yatirimradar.paper_trading.gunluk import tahminleri_kaydet
        from yatirimradar.scoring.sirala import firsatlari_sirala

        kesim = datetime.now(UTC)
        siralama = firsatlari_sirala(as_of=kesim, limit=20)
        hedef = get_settings().data_processed_dir / "tahminler"
        yol = tahminleri_kaydet(siralama, kesim, None, hedef)
        typer.echo(f"[daily] Ranking snapshot: {yol.name}")

    if hatalar:
        typer.echo(f"[daily] Steps with errors: {', '.join(hatalar)}", err=True)
        raise typer.Exit(1)
    typer.echo("[daily] Complete")


@app.command("serve")
def serve(
    host: str = typer.Option("127.0.0.1", help="Server host."),
    port: int = typer.Option(8000, help="Server port."),
) -> None:
    """Start the local API and map server."""
    import uvicorn

    typer.echo(f"YatırımRadar map: http://{host}:{port}/")
    uvicorn.run("yatirimradar.api.main:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    app()
