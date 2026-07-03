#!/usr/bin/env python3
"""test_templates.py — Tests del lado servidor (sin WRF instalado).

Cubre: render de templates Jinja2, rango de meses ERA5 (spin-up en anio
anterior), cobertura era5.area vs d01, y el parcheo de namelist para
reanudar WRF desde checkpoint.
"""
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

sys.path.insert(0, str(Path(__file__).parent / "workflow" / "scripts"))
from download_era5 import meses_periodo, dias_mes_en_periodo
from check_config import era5_area_minima
from prepare_restart import patch_namelist


def _ctx(cfg):
    inicio_str = cfg["periodo"]["inicio"].split("_")
    fin_str = cfg["periodo"]["fin"].split("_")
    inicio_dt = datetime.strptime(inicio_str[0], "%Y-%m-%d")
    inicio_time = datetime.strptime(inicio_str[1], "%H:%M:%S")
    fin_dt = datetime.strptime(fin_str[0], "%Y-%m-%d")
    fin_time = datetime.strptime(fin_str[1], "%H:%M:%S")
    dur = (datetime.combine(fin_dt.date(), fin_time.time())
           - datetime.combine(inicio_dt.date(), inicio_time.time()))
    return {
        "proyecto": cfg["proyecto"], "wrf": cfg["wrf"], "periodo": cfg["periodo"],
        "rutas": cfg.get("rutas", {}), "validacion": cfg.get("validacion", {}),
        "inicio_year": inicio_dt.year, "inicio_month": inicio_dt.month,
        "inicio_day": inicio_dt.day, "inicio_hour": inicio_time.hour,
        "inicio_minute": inicio_time.minute, "inicio_second": inicio_time.second,
        "fin_year": fin_dt.year, "fin_month": fin_dt.month, "fin_day": fin_dt.day,
        "fin_hour": fin_time.hour, "fin_minute": fin_time.minute,
        "fin_second": fin_time.second,
        "run_days": dur.days, "run_hours": dur.seconds // 3600,
    }


def test_templates_render():
    """Los templates del servidor renderizan sin variables sueltas."""
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)
    env = Environment(loader=FileSystemLoader("static"), undefined=StrictUndefined)
    ok = True
    for tpl_name in ["namelist.wps.j2", "namelist.input.j2"]:
        try:
            rendered = env.get_template(tpl_name).render(**_ctx(cfg))
            assert len(rendered) > 100
            print(f"[OK] {tpl_name} rendered ({len(rendered)} chars)")
        except Exception as e:
            print(f"[FAIL] {tpl_name}: {e}")
            ok = False
    return ok


def test_meses_periodo():
    """El rango de meses ERA5 incluye el spin-up del anio anterior."""
    pares = meses_periodo("2023-12-29_00:00:00", "2024-12-31_18:00:00")
    assert pares[0] == (2023, 12), f"Falta el mes del spin-up: {pares[0]}"
    assert pares[-1] == (2024, 12)
    assert len(pares) == 13
    assert meses_periodo("2024-03-01_00:00:00", "2024-03-05_00:00:00") == [(2024, 3)]
    print("[OK] meses_periodo cubre el spin-up (13 meses: 2023-12 .. 2024-12)")
    return True


def test_dias_mes_en_periodo():
    """Meses de borde: solo se bajan los dias dentro de inicio..fin."""
    ini, fin = "2025-05-29_00:00:00", "2026-06-01_00:00:00"
    assert dias_mes_en_periodo(2025, 5, ini, fin) == ["29", "30", "31"]
    assert dias_mes_en_periodo(2026, 6, ini, fin) == ["01"]
    assert len(dias_mes_en_periodo(2025, 7, ini, fin)) == 31  # mes interior completo
    pares = meses_periodo(ini, fin)
    assert pares[0] == (2025, 5) and pares[-1] == (2026, 6) and len(pares) == 14
    print("[OK] dias_mes_en_periodo recorta los meses de borde (may: 3 dias, jun: 1 dia)")
    return True


def test_era5_area_cubre_d01():
    """El area ERA5 del config cubre el dominio exterior d01."""
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)
    n, w, s, e = cfg["era5"]["area"]
    n_min, w_min, s_min, e_min = era5_area_minima(cfg["wrf"])
    assert n >= n_min and s <= s_min and w <= w_min and e >= e_min, (
        f"era5.area {cfg['era5']['area']} no cubre d01 "
        f"(minimo [{n_min:.2f}, {w_min:.2f}, {s_min:.2f}, {e_min:.2f}])")
    print(f"[OK] era5.area cubre d01 (minimo [{n_min:.2f}, {w_min:.2f}, {s_min:.2f}, {e_min:.2f}])")
    return True


def test_prepare_restart_patch():
    """El parcheo de restart fija restart=.true., start_*=checkpoint y run_*=0."""
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)
    env = Environment(loader=FileSystemLoader("static"), undefined=StrictUndefined)
    rendered = env.get_template("namelist.input.j2").render(**_ctx(cfg))

    with tempfile.NamedTemporaryFile("w", suffix=".input", delete=False) as f:
        f.write(rendered)
        tmp = f.name
    try:
        patch_namelist(tmp, "2024-03-05_06:00:00")
        out = Path(tmp).read_text()
        import re
        assert re.search(r"^\s*restart\s*=\s*\.true\.,", out, re.M), \
            "restart no quedo en .true."
        assert any("start_year" in l and "2024,2024,2024," in l.replace(" ", "")
                   for l in out.splitlines()), "start_year no apunta al checkpoint"
        assert any("start_hour" in l and "6,6,6," in l.replace(" ", "")
                   for l in out.splitlines()), "start_hour no apunta al checkpoint"
        assert any("run_days" in l and "= 0," in l for l in out.splitlines()), \
            "run_days debe quedar en 0 (si no, WRF corre run_days DESDE el checkpoint)"
        # restart_interval NO debe haberse tocado
        assert any("restart_interval" in l and "360" in l for l in out.splitlines()), \
            "restart_interval fue alterado por el regex de restart"
        print("[OK] prepare_restart parcha restart/start_*/run_* sin tocar restart_interval")
        return True
    finally:
        os.unlink(tmp)


if __name__ == "__main__":
    os.chdir(Path(__file__).parent)
    ok = True
    ok &= test_templates_render()
    ok &= test_meses_periodo()
    ok &= test_dias_mes_en_periodo()
    ok &= test_era5_area_cubre_d01()
    ok &= test_prepare_restart_patch()
    if ok:
        print("\nAll tests passed.")
        sys.exit(0)
    print("\nSome tests failed.")
    sys.exit(1)
