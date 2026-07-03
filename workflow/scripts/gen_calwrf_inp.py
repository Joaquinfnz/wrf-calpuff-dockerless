#!/usr/bin/env python3
"""
gen_calwrf_inp.py — Genera data/calwrf/calwrf.inp para CALWRF.

CALWRF convierte los wrfout (~40 GB) en un 3D.DAT (~4 GB) que lee CALMET.
Por defecto procesa el dominio fino (d03, 1 km) sobre el area del proyecto.

Formato calwrf.inp (CALWRF v2.0.3):
  linea 1: archivo log
  linea 2: archivo de salida 3D.DAT
  linea 3: beg/end I,J,K (0 = dominio completo)
  linea 4: fecha inicio UTC (YYYYMMDDHH)
  linea 5: fecha fin UTC (YYYYMMDDHH)
  linea 6: numero de archivos wrfout
  lineas 7+: rutas de los wrfout
"""
import sys
import yaml
from pathlib import Path
from datetime import datetime


def _utc_stamp(s):
    fecha, hora = s.split("_")
    return datetime.strptime(f"{fecha} {hora}", "%Y-%m-%d %H:%M:%S").strftime("%Y%m%d%H")


def main(config_path, dominio="d03"):
    cfg = yaml.safe_load(open(config_path))
    ini = _utc_stamp(cfg["periodo"]["inicio"])
    fin = _utc_stamp(cfg["periodo"]["fin"])

    # Excluir el spin-up del 3D.DAT: CALMET/CALPUFF solo deben ver el año
    # evaluado (desde periodo.inicio_evaluacion), no los dias de estabilizacion.
    ini_eval = cfg["periodo"].get("inicio_evaluacion")
    if ini_eval:
        ini = max(ini, _utc_stamp(ini_eval))

    wrf = sorted(p for p in Path("data/wrf").glob(f"wrfout_{dominio}_*")
                 if not p.name.endswith(".log"))
    if not wrf:
        sys.exit(f"[ERROR] No hay wrfout_{dominio}_* en data/wrf/")

    out = Path("data/calwrf")
    out.mkdir(parents=True, exist_ok=True)
    lineas = ["calwrf.lst", "3d.dat", "0 0 0 0 0 0", ini, fin, str(len(wrf))]
    # Rutas absolutas del host (sin Docker ya no existe el mount /data)
    lineas += [str(p.resolve()) for p in wrf]
    (out / "calwrf.inp").write_text("\n".join(lineas) + "\n")
    print(f"[OK] data/calwrf/calwrf.inp — {len(wrf)} archivos {dominio} ({ini}->{fin})")


if __name__ == "__main__":
    dom = sys.argv[2] if len(sys.argv) > 2 else "d03"
    if len(sys.argv) < 2:
        print("Uso: python3 gen_calwrf_inp.py config.yaml [d01|d02|d03]")
        sys.exit(1)
    main(sys.argv[1], dom)
