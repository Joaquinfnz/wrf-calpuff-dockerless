#!/usr/bin/env python3
"""
prepare_restart.py — Reanudacion automatica de WRF tras una caida.

Antes, el bucle de reintento relanzaba wrf.exe con restart=.false. fijo:
los checkpoints (wrfrst_* cada 6 h) se escribian pero NUNCA se usaban y
cada reintento partia desde cero (quemando credito AWS).

Este script corre antes de cada intento (rule wrf del Snakefile):
  - Busca el ultimo checkpoint completo (wrfrst_d0N_* para TODOS los dominios).
  - Si existe, parcha namelist.input: restart=.true., start_* = hora del
    checkpoint y run_* = 0 (con run_days>0 WRF correria run_days completos
    DESDE el checkpoint, pasandose del end_* original).
  - Si no hay checkpoints, no toca nada (primera corrida, restart=.false.).

Idempotente: llamarlo N veces deja el namelist apuntando al ultimo checkpoint.

Uso: python3 prepare_restart.py data/wrf/namelist.input data/wrf
"""
import re
import sys
from pathlib import Path


def ultimo_checkpoint(wrf_dir, ndom):
    """Timestamp del checkpoint mas reciente que tiene wrfrst de TODOS los
    dominios (WRF anidado exige reanudar todos a la misma hora)."""
    stamps = sorted(
        (p.name.replace("wrfrst_d01_", "") for p in Path(wrf_dir).glob("wrfrst_d01_*")),
        reverse=True,
    )
    for stamp in stamps:
        if all((Path(wrf_dir) / f"wrfrst_d{d:02d}_{stamp}").exists()
               for d in range(1, ndom + 1)):
            return stamp
    return None


def patch_namelist(namelist_path, stamp):
    txt = Path(namelist_path).read_text()
    ndom = int(re.search(r"max_dom\s*=\s*(\d+)", txt).group(1))

    fecha, hora = stamp.split("_")     # 2024-03-05_06:00:00
    y, mo, d = fecha.split("-")
    h, mi, s = hora.split(":")
    campos = {
        "start_year": y, "start_month": mo, "start_day": d,
        "start_hour": h, "start_minute": mi, "start_second": s,
    }
    for k, v in campos.items():
        val = (str(int(v)) + ",") * ndom
        txt = re.sub(rf"(^\s*{k}\s*=\s*)[^\n]*", rf"\g<1>{val}", txt, flags=re.M)

    # restart = .true.  (el \s*= no matchea restart_interval)
    txt = re.sub(r"(^\s*restart\s*=\s*)\.\w+\.,?", r"\g<1>.true.,", txt, flags=re.M)

    # run_* = 0 para que el fin lo defina end_* (fijo), no una duracion relativa
    for k in ("run_days", "run_hours", "run_minutes", "run_seconds"):
        txt = re.sub(rf"(^\s*{k}\s*=\s*)[^\n]*", r"\g<1>0,", txt, flags=re.M)

    Path(namelist_path).write_text(txt)


def main():
    if len(sys.argv) < 3:
        print("Uso: python3 prepare_restart.py namelist.input dir_wrf")
        sys.exit(1)
    namelist, wrf_dir = sys.argv[1], sys.argv[2]

    txt = Path(namelist).read_text()
    ndom = int(re.search(r"max_dom\s*=\s*(\d+)", txt).group(1))

    stamp = ultimo_checkpoint(wrf_dir, ndom)
    if stamp is None:
        print("[INFO] Sin checkpoints wrfrst_*: corrida desde el inicio (restart=.false.)")
        return
    patch_namelist(namelist, stamp)
    print(f"[INFO] Reanudando WRF desde checkpoint {stamp} (restart=.true.)")


if __name__ == "__main__":
    main()
