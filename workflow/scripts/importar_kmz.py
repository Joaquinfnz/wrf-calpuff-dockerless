#!/usr/bin/env python3
"""
importar_kmz.py — Configura el dominio WRF desde el KMZ/KML del proyecto.

Automatiza el paso que antes se hacia a mano: "poner las coordenadas del KMZ
en el namelist". Lee el/los poligono(s) del proyecto, calcula el centroide y el
bounding box, y deriva:
  - centro del dominio:  ref_lat, ref_lon, stand_lon (= ref_lon)
  - true latitudes:      truelat1, truelat2 (bracket del centroide)
  - area ERA5 [N,W,S,E]: cubre el dominio exterior d01 + margen

Uso:
    python3 importar_kmz.py proyecto.kmz                      # dry-run, imprime
    python3 importar_kmz.py proyecto.kmz --apply config.yaml  # parcha config.yaml
"""

import re
import sys
import math
import zipfile
import shutil
from pathlib import Path

COORD_RE = re.compile(r"<coordinates>(.*?)</coordinates>", re.DOTALL | re.IGNORECASE)


def _read_kml_text(path: Path) -> str:
    if path.suffix.lower() == ".kmz":
        with zipfile.ZipFile(path) as z:
            name = next((n for n in z.namelist() if n.lower().endswith(".kml")), None)
            if not name:
                sys.exit(f"[ERROR] {path} no contiene ningun .kml")
            return z.read(name).decode("utf-8", errors="replace")
    return path.read_text(encoding="utf-8", errors="replace")


def _vertices(kml_text):
    pts = []
    for block in COORD_RE.findall(kml_text):
        for tok in block.split():
            parts = tok.split(",")
            if len(parts) >= 2:
                try:
                    pts.append((float(parts[0]), float(parts[1])))
                except ValueError:
                    continue
    return pts


def domain_from_kml(path, e_we=61, res_km=9.0):
    pts = _vertices(_read_kml_text(Path(path)))
    if not pts:
        sys.exit(f"[ERROR] No se encontraron coordenadas en {path}")
    lons = [p[0] for p in pts]
    lats = [p[1] for p in pts]
    cen_lon, cen_lat = sum(lons) / len(lons), sum(lats) / len(lats)

    # Semi-extension del dominio exterior d01 (km -> grados) + margen
    half_km = e_we * res_km / 2.0
    dlat = half_km / 111.0
    dlon = half_km / (111.0 * math.cos(math.radians(cen_lat)))
    buf = 0.5

    return {
        "n_vertices": len(pts),
        "ref_lat": round(cen_lat, 4),
        "ref_lon": round(cen_lon, 4),
        "stand_lon": round(cen_lon, 4),
        "truelat1": round(cen_lat + 1.5, 2),
        "truelat2": round(cen_lat - 1.5, 2),
        "area": [round(max(lats) + dlat + buf, 2), round(min(lons) - dlon - buf, 2),
                 round(min(lats) - dlat - buf, 2), round(max(lons) + dlon + buf, 2)],
        "bbox": (round(min(lons), 4), round(min(lats), 4),
                 round(max(lons), 4), round(max(lats), 4)),
    }


def apply_to_config(d, config_path):
    p = Path(config_path)
    shutil.copy(p, str(p) + ".bak")
    txt = p.read_text()
    repl = {
        r"(^\s*ref_lat:\s*)[-\d.]+":   rf"\g<1>{d['ref_lat']}",
        r"(^\s*ref_lon:\s*)[-\d.]+":   rf"\g<1>{d['ref_lon']}",
        r"(^\s*stand_lon:\s*)[-\d.]+": rf"\g<1>{d['stand_lon']}",
        r"(^\s*truelat1:\s*)[-\d.]+":  rf"\g<1>{d['truelat1']}",
        r"(^\s*truelat2:\s*)[-\d.]+":  rf"\g<1>{d['truelat2']}",
        r"(^\s*area:\s*)\[[^\]]*\]":   rf"\g<1>{d['area']}",
    }
    for pat, rep in repl.items():
        txt = re.sub(pat, rep, txt, count=1, flags=re.MULTILINE)
    p.write_text(txt)
    print(f"[OK] {p.name} actualizado (backup en {p.name}.bak)")


def main():
    args = sys.argv[1:]
    if not args:
        print("Uso: python3 importar_kmz.py proyecto.kmz [--apply config.yaml]")
        sys.exit(1)
    d = domain_from_kml(args[0])
    print(f"  Vertices leidos    : {d['n_vertices']}")
    print(f"  BBox (W,S,E,N)     : {d['bbox']}")
    print(f"  ref_lat            : {d['ref_lat']}")
    print(f"  ref_lon            : {d['ref_lon']}")
    print(f"  stand_lon          : {d['stand_lon']}")
    print(f"  truelat1 / truelat2: {d['truelat1']} / {d['truelat2']}")
    print(f"  era5 area [N,W,S,E]: {d['area']}")
    if "--apply" in args:
        i = args.index("--apply")
        cfg = args[i + 1] if i + 1 < len(args) else "config.yaml"
        apply_to_config(d, cfg)


if __name__ == "__main__":
    main()
