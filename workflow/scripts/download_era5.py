#!/usr/bin/env python3
"""
download_era5.py — Descarga ERA5 para WRF/WPS desde el Climate Data Store (CDS).

Baja los DOS datasets estandar que WPS/ungrib necesita y los concatena en un
solo GRIB por mes (ungrib lee todos los mensajes de un archivo):
  - reanalysis-era5-pressure-levels  -> campos 3D (geopotencial, T, U, V, HR, q)
  - reanalysis-era5-single-levels    -> superficie + suelo (4 capas) + SST + mascara

Salida: data/raw/ERA5_{anio}_{mes:02d}.grib  (nombre que espera el Snakefile).

CDS nuevo (migracion 2024): endpoint https://cds.climate.copernicus.eu/api con un
unico Token de Acceso Personal (PAT). El viejo /api/v2 (uid:key) fue retirado.
Token en: https://cds.climate.copernicus.eu/profile
"""

import os
import sys
import time
import yaml
import calendar
from pathlib import Path

# cdsapi se importa dentro de download_era5(): el Snakefile importa
# meses_periodo() de este modulo y no debe exigir cdsapi instalado.


def meses_periodo(inicio, fin):
    """Pares (anio, mes) que cubren periodo.inicio..fin (incluye el spin-up,
    que puede caer en el anio/mes anterior al año evaluado)."""
    yi, mi = int(inicio[:4]), int(inicio[5:7])
    yf, mf = int(fin[:4]), int(fin[5:7])
    pares = []
    y, m = yi, mi
    while (y, m) <= (yf, mf):
        pares.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return pares


def dias_mes_en_periodo(anio, mes, inicio, fin):
    """Dias del mes que caen dentro de inicio..fin. En los meses de borde
    (p.ej. el mes del spin-up o el dia final) evita bajar el mes completo."""
    ndias = calendar.monthrange(anio, mes)[1]
    d_ini, d_fin = 1, ndias
    yi, mi, di = int(inicio[:4]), int(inicio[5:7]), int(inicio[8:10])
    yf, mf, df = int(fin[:4]), int(fin[5:7]), int(fin[8:10])
    if (anio, mes) == (yi, mi):
        d_ini = di
    if (anio, mes) == (yf, mf):
        d_fin = df
    return [f"{d:02d}" for d in range(d_ini, d_fin + 1)]

# 37 niveles de presion estandar de ERA5 (hPa). => num_metgrid_levels = 38 (37 + sup).
PRESSURE_LEVELS = [
    "1000", "975", "950", "925", "900", "875", "850", "825", "800", "775",
    "750", "700", "650", "600", "550", "500", "450", "400", "350", "300",
    "250", "225", "200", "175", "150", "125", "100", "70", "50", "30",
    "20", "10", "7", "5", "3", "2", "1",
]

# Campos 3D en niveles de presion (nombres CDS)
PL_VARS = [
    "geopotential", "temperature",
    "u_component_of_wind", "v_component_of_wind",
    "relative_humidity", "specific_humidity",
]

# Superficie + suelo que real.exe necesita (sin esto la corrida queda incompleta)
SFC_VARS = [
    "10m_u_component_of_wind", "10m_v_component_of_wind",
    "2m_temperature", "2m_dewpoint_temperature",
    "mean_sea_level_pressure", "surface_pressure",
    "sea_surface_temperature", "skin_temperature",
    "soil_temperature_level_1", "soil_temperature_level_2",
    "soil_temperature_level_3", "soil_temperature_level_4",
    "volumetric_soil_water_layer_1", "volumetric_soil_water_layer_2",
    "volumetric_soil_water_layer_3", "volumetric_soil_water_layer_4",
    "land_sea_mask", "sea_ice_cover", "snow_depth",
    "geopotential",  # geopotencial de superficie (orografia), invariante pero requerido
]

NEW_CDS_URL = "https://cds.climate.copernicus.eu/api"


def _ensure_cdsapirc(cfg_era5):
    """Escribe ~/.cdsapirc desde CDSAPI_KEY/config, o usa el existente."""
    key = os.environ.get("CDSAPI_KEY", cfg_era5.get("cds_api_key", "")).strip()
    url = (cfg_era5.get("cds_api_url") or NEW_CDS_URL).strip()
    if not key:
        if (Path.home() / ".cdsapirc").exists():
            return
        print("[ERROR] Falta el token CDS.")
        print("  export CDSAPI_KEY='<tu-token-de-acceso-personal>'")
        print("  Token en: https://cds.climate.copernicus.eu/profile")
        sys.exit(1)
    rc = Path.home() / ".cdsapirc"
    rc.write_text(f"url: {url}\nkey: {key}\n")
    rc.chmod(0o600)


def _retrieve(client, dataset, request, target, max_retries=5):
    for attempt in range(1, max_retries + 1):
        try:
            client.retrieve(dataset, request, str(target))
            return
        except Exception as e:  # cdsapi lanza varias; reintentar con backoff
            print(f"  [RETRY {attempt}/{max_retries}] {dataset}: {e}")
            time.sleep(30 * attempt)
    print(f"  [FAIL] No se pudo descargar {dataset} -> {target}")
    sys.exit(1)


def download_era5(config_path, solo=None):
    """Descarga los meses que cubren periodo.inicio..fin (spin-up incluido).
    Con solo="YYYY-MM" baja unicamente ese mes (para la rule por-mes del
    Snakefile: una descarga fallida no invalida los meses ya bajados)."""
    import cdsapi

    with open(config_path) as f:
        cfg = yaml.safe_load(f)

    era5 = cfg["era5"]
    area = era5["area"]   # [N, W, S, E]
    grid = era5["grid"]   # [dlat, dlon]
    raw = Path("data/raw")
    raw.mkdir(parents=True, exist_ok=True)

    pares = meses_periodo(cfg["periodo"]["inicio"], cfg["periodo"]["fin"])
    if solo:
        y, m = int(solo[:4]), int(solo[5:7])
        pares = [(y, m)]

    _ensure_cdsapirc(era5)
    c = cdsapi.Client()

    inicio = cfg["periodo"]["inicio"]
    fin = cfg["periodo"]["fin"]
    for anio, mes in pares:
        final = raw / f"ERA5_{anio}_{mes:02d}.grib"
        if final.exists():
            print(f"[SKIP] {final} ya existe")
            continue

        dias = dias_mes_en_periodo(anio, mes, inicio, fin)
        horas = [f"{h:02d}:00" for h in range(0, 24, 6)]  # 6-horario para WPS
        print(f"[DOWNLOAD] ERA5 {anio}-{mes:02d} ({len(dias)} dias x {len(horas)} h)")

        pl = raw / f"ERA5_{anio}_{mes:02d}_pl.grib"
        sfc = raw / f"ERA5_{anio}_{mes:02d}_sfc.grib"

        _retrieve(c, "reanalysis-era5-pressure-levels", {
            "product_type": "reanalysis",
            "variable": PL_VARS,
            "pressure_level": PRESSURE_LEVELS,
            "year": str(anio), "month": f"{mes:02d}", "day": dias, "time": horas,
            "area": area, "grid": grid, "data_format": "grib",
        }, pl)

        _retrieve(c, "reanalysis-era5-single-levels", {
            "product_type": "reanalysis",
            "variable": SFC_VARS,
            "year": str(anio), "month": f"{mes:02d}", "day": dias, "time": horas,
            "area": area, "grid": grid, "data_format": "grib",
        }, sfc)

        # ungrib lee todos los mensajes de un GRIB -> concatenar sup + presion
        # (por streaming: el pl mensual puede pesar varios GB)
        import shutil
        with open(final, "wb") as out:
            for parte in (sfc, pl):
                with open(parte, "rb") as src:
                    shutil.copyfileobj(src, out)
        pl.unlink()
        sfc.unlink()
        print(f"  [OK] {final}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python3 download_era5.py config.yaml [--solo YYYY-MM]")
        sys.exit(1)
    solo = None
    if "--solo" in sys.argv:
        solo = sys.argv[sys.argv.index("--solo") + 1]
    download_era5(sys.argv[1], solo=solo)
