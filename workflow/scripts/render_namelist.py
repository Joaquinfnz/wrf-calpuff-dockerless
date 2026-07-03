#!/usr/bin/env python3
"""
render_namelist.py — Renderiza namelists desde templates Jinja2
Genera: namelist.wps, namelist.input, calmet.inp, calpuff.inp
usando config.yaml
"""
import os
import sys
import yaml
from datetime import datetime
from pathlib import Path

try:
    from jinja2 import Environment, BaseLoader
except ImportError:
    print("[INFO] Instalando Jinja2...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "jinja2", "-q"])
    from jinja2 import Environment, BaseLoader


def render_namelists(config_path):
    with open(config_path) as f:
        cfg = yaml.safe_load(f)

    wrf = cfg["wrf"]
    periodo = cfg["periodo"]
    proyecto = cfg["proyecto"]
    calmet = cfg.get("calmet", {})
    calpuff = cfg.get("calpuff", {})
    # Expandir ~ (setup dockerless instala en $HOME; geogrid necesita ruta absoluta)
    rutas = {k: (os.path.expanduser(v) if isinstance(v, str) else v)
             for k, v in cfg["rutas"].items()}
    validacion = cfg.get("validacion", {})

    # ── Parsear fechas ──────────────────────────────────────────────────────
    inicio_str = periodo["inicio"].split("_")
    fin_str = periodo["fin"].split("_")

    inicio_dt = datetime.strptime(inicio_str[0], "%Y-%m-%d")
    inicio_time = datetime.strptime(inicio_str[1], "%H:%M:%S")
    fin_dt = datetime.strptime(fin_str[0], "%Y-%m-%d")
    fin_time = datetime.strptime(fin_str[1], "%H:%M:%S")

    # Duracion total de la corrida WRF (para run_days/run_hours en namelist.input)
    ini_full = datetime.combine(inicio_dt.date(), inicio_time.time())
    fin_full = datetime.combine(fin_dt.date(), fin_time.time())
    dur = fin_full - ini_full

    ctx = {
        "wrf": wrf,
        "periodo": periodo,
        "proyecto": proyecto,
        "calmet": calmet,
        "calpuff": calpuff,
        "rutas": rutas,
        "validacion": validacion,
        # Fechas parseadas para namelists
        "inicio_year": inicio_dt.year,
        "inicio_month": inicio_dt.month,
        "inicio_day": inicio_dt.day,
        "inicio_hour": inicio_time.hour,
        "inicio_minute": inicio_time.minute,
        "inicio_second": inicio_time.second,
        "fin_year": fin_dt.year,
        "fin_month": fin_dt.month,
        "fin_day": fin_dt.day,
        "fin_hour": fin_time.hour,
        "fin_minute": fin_time.minute,
        "fin_second": fin_time.second,
        "run_days": dur.days,
        "run_hours": dur.seconds // 3600,
    }

    # ── Templates ───────────────────────────────────────────────────────────
    templates_dir = Path("static")
    templates = {
        "namelist.wps": "data/wps/namelist.wps",
        "namelist.input": "data/wrf/namelist.input",
    }  # Solo servidor: WRF. CALMET/CALPUFF se generan en la PC.

    env = Environment(loader=BaseLoader())

    for template_name, out_path in templates.items():
        template_file = templates_dir / f"{template_name}.j2"
        if not template_file.exists():
            print(f"[WARN] Template {template_file} no encontrado, saltando")
            continue

        template_src = template_file.read_text()
        template = env.from_string(template_src)

        rendered = template.render(**ctx)

        out_file = Path(out_path)
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(rendered)
        print(f"[OK] {out_path}")

    print(f"\n[OK] {len(templates)} namelists renderizados.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python3 render_namelist.py config.yaml")
        sys.exit(1)
    render_namelists(sys.argv[1])
