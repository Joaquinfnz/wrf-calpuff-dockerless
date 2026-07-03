#!/usr/bin/env python3
"""
validar_wrf.py — Validacion meteorologica WRF vs observaciones + post-proceso.

Genera los productos que exige la Guia SEA "Uso de modelos de calidad del aire
en el SEIA" (2a ed., feb-2023) para la meteorologia del modelo de pronostico:

  6.6/6.7  Series de tiempo obs vs mod (T2, WS10, WD10)  -> validacion_wrf.png
           Ciclos diarios promedio (hora local)          -> ciclos_diarios.png
           Ciclo estacional (medias mensuales)           -> ciclo_estacional.png
           Rosas de viento obs y mod                     -> rosas_viento.png
           Mapas de viento d03 diurno/nocturno x
           verano/invierno (promedios U10/V10)           -> mapas_viento.png
  6.8/7    Metricas cuantitativas: sesgo, r, RMSE (minimo guia) + MAE, IOA,
           FB, NMSE, R2; globales, por estacion del año y dia/noche
                                        -> metricas_validacion.csv / .tex

Config (bloque validacion): observaciones (csv con columna fecha + t2/ws10/wd10),
dominio (default d03), estacion_lat/lon (celda mas cercana; sin ella usa el
centro), obs_utc_offset (hora local de las obs respecto a UTC; tambien define
la hora local de ciclos diarios y dia/noche).

El periodo de spin-up (antes de periodo.inicio_evaluacion) se excluye de
metricas y graficos.
"""

import sys
import yaml
from datetime import datetime
from pathlib import Path
import numpy as np
import pandas as pd
import xarray as xr
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

# Estaciones del año (hemisferio sur)
ESTACIONES = {
    "verano": (12, 1, 2), "otono": (3, 4, 5),
    "invierno": (6, 7, 8), "primavera": (9, 10, 11),
}
HORAS_DIA = range(8, 20)  # dia local: 08:00-19:59; resto = noche


def compute_metrics(obs, mod, circular=False):
    """Metricas de validacion. circular=True (direccion de viento): errores
    con la diferencia angular minima (350 vs 10 = 20 grados, no 340)."""
    valid = ~np.isnan(obs) & ~np.isnan(mod)
    o = np.asarray(obs)[valid]
    m = np.asarray(mod)[valid]

    if len(o) < 10:
        return {"n": 0, "rmse": np.nan, "mae": np.nan, "sesgo": np.nan,
                "r": np.nan, "ioa": np.nan, "fb": np.nan, "nmse": np.nan, "r2": np.nan}

    if circular:
        d = ((m - o + 180.0) % 360.0) - 180.0
        return {"n": len(o), "rmse": np.sqrt(np.mean(d ** 2)),
                "mae": np.mean(np.abs(d)), "sesgo": np.mean(d),
                "r": np.nan, "ioa": np.nan, "fb": np.nan, "nmse": np.nan, "r2": np.nan}

    rmse = np.sqrt(np.mean((m - o) ** 2))
    mae = np.mean(np.abs(m - o))
    sesgo = np.mean(m - o)
    r = np.corrcoef(o, m)[0, 1]

    # Index of Agreement (Willmott 1981)
    o_mean = np.mean(o)
    den = np.sum((np.abs(m - o_mean) + np.abs(o - o_mean)) ** 2)
    ioa = 1 - np.sum((m - o) ** 2) / den if den > 0 else np.nan

    fb = 2 * (np.mean(m) - np.mean(o)) / (np.mean(m) + np.mean(o)) if (np.mean(m) + np.mean(o)) != 0 else np.nan
    nmse = np.mean((m - o) ** 2) / (np.mean(m) * np.mean(o)) if np.mean(m) * np.mean(o) != 0 else np.nan

    return {"n": len(o), "rmse": rmse, "mae": mae, "sesgo": sesgo,
            "r": r, "ioa": ioa, "fb": fb, "nmse": nmse, "r2": r ** 2}


def _open_wrfouts(files):
    """Abre los wrfout con un indice temporal datetime64 en la dim Time."""
    try:
        ds = xr.open_mfdataset(files, combine="by_coords")
    except Exception:
        ds = xr.open_mfdataset(files, combine="nested", concat_dim="Time")

    if "XTIME" in ds.variables and np.issubdtype(ds["XTIME"].dtype, np.datetime64):
        times = pd.to_datetime(np.asarray(ds["XTIME"].values))
    else:
        filas = []
        for row in np.asarray(ds["Times"].values):
            s = b"".join(row).decode() if row.dtype.kind == "S" else "".join(map(str, row))
            filas.append(s)
        times = pd.to_datetime(filas, format="%Y-%m-%d_%H:%M:%S")
    return ds.assign_coords(Time=("Time", times))


def _punto_estacion(ds, val_cfg):
    """(j, i) de la celda mas cercana a la estacion. Sin estacion configurada
    usa el centro del dominio."""
    lat = val_cfg.get("estacion_lat")
    lon = val_cfg.get("estacion_lon")
    ny, nx = ds.sizes["south_north"], ds.sizes["west_east"]
    if lat is None or lon is None:
        print("[WARN] validacion.estacion_lat/lon no definidos: uso el centro del dominio")
        return ny // 2, nx // 2
    xlat = np.asarray(ds["XLAT"].isel(Time=0).values)
    xlon = np.asarray(ds["XLONG"].isel(Time=0).values)
    dist2 = (xlat - lat) ** 2 + ((xlon - lon) * np.cos(np.radians(lat))) ** 2
    j, i = np.unravel_index(np.argmin(dist2), dist2.shape)
    print(f"[INFO] Estacion ({lat}, {lon}) -> celda (j={j}, i={i}), "
          f"lat/lon de celda: ({xlat[j, i]:.3f}, {xlon[j, i]:.3f})")
    return int(j), int(i)


def _segmentos(idx_utc, offset):
    """Mascaras por estacion del año y dia/noche (en hora local)."""
    local = idx_utc + pd.Timedelta(hours=offset)
    seg = {"global": pd.Series(True, index=idx_utc)}
    for nombre, meses in ESTACIONES.items():
        seg[nombre] = pd.Series(local.month.isin(meses), index=idx_utc)
    es_dia = pd.Series(local.hour.isin(HORAS_DIA), index=idx_utc)
    seg["dia"] = es_dia
    seg["noche"] = ~es_dia
    return seg


def _wd_media_circular(wd_series, por):
    """Media circular de direccion agrupada por `por` (p.ej. hora local)."""
    rad = np.radians(wd_series)
    u = pd.Series(-np.sin(rad), index=wd_series.index)
    v = pd.Series(-np.cos(rad), index=wd_series.index)
    um, vm = u.groupby(por).mean(), v.groupby(por).mean()
    return (270.0 - np.degrees(np.arctan2(vm, um))) % 360.0


def _plot_series(series, obs_df, out_dir, proyecto, etiqueta, metricas_globales):
    fig, axes = plt.subplots(3, 1, figsize=(12, 15), sharex=True)
    for ax, (var, info) in zip(axes, series.items()):
        ax.plot(info["mod"].index, info["mod"].values, "b-", alpha=0.7,
                label="WRF", linewidth=0.5)
        if info["obs"] is not None:
            ax.plot(info["obs"].index, info["obs"].values, "r.", alpha=0.5,
                    label="Obs", markersize=2)
            m = metricas_globales.get(var)
            if m:
                ax.text(0.02, 0.95,
                        f"Sesgo={m['sesgo']:.2f}  r={m['r']:.2f}  RMSE={m['rmse']:.2f}\n"
                        f"MAE={m['mae']:.2f}  IOA={m['ioa']:.2f}  N={m['n']}",
                        transform=ax.transAxes, fontsize=8, verticalalignment="top",
                        bbox=dict(boxstyle="round", facecolor="white", alpha=0.8))
        ax.set_ylabel(info["units"])
        ax.set_title(info["title"])
        ax.legend(loc="lower right", fontsize=8)
        ax.grid(True, alpha=0.3)
    axes[-1].xaxis.set_major_locator(mdates.MonthLocator())
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    plt.xticks(rotation=45)
    fig.suptitle(f"Series de tiempo WRF vs Obs — {proyecto} {etiqueta}",
                 fontsize=14, fontweight="bold")
    fig.tight_layout()
    fig.savefig(out_dir / "validacion_wrf.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"[OK] {out_dir}/validacion_wrf.png")


def _plot_ciclos_diarios(series, offset, out_dir):
    """Ciclo diario promedio por hora local, obs vs mod (guia 6.6.3/6.7)."""
    fig, axes = plt.subplots(1, 3, figsize=(16, 4.5))
    for ax, (var, info) in zip(axes, series.items()):
        hora_mod = (info["mod"].index + pd.Timedelta(hours=offset)).hour
        if var == "WD10":
            cm = _wd_media_circular(info["mod"], hora_mod)
            ax.plot(cm.index, cm.values, "b-o", ms=3, label="WRF")
            if info["obs"] is not None:
                hora_obs = (info["obs"].index + pd.Timedelta(hours=offset)).hour
                co = _wd_media_circular(info["obs"].dropna(), hora_obs[~info["obs"].isna()])
                ax.plot(co.index, co.values, "r-s", ms=3, label="Obs")
            ax.set_ylim(0, 360)
            ax.set_yticks([0, 90, 180, 270, 360])
        else:
            cm = info["mod"].groupby(hora_mod).mean()
            ax.plot(cm.index, cm.values, "b-o", ms=3, label="WRF")
            if info["obs"] is not None:
                hora_obs = (info["obs"].index + pd.Timedelta(hours=offset)).hour
                co = info["obs"].groupby(hora_obs).mean()
                ax.plot(co.index, co.values, "r-s", ms=3, label="Obs")
        ax.set_xlabel("Hora local")
        ax.set_ylabel(info["units"])
        ax.set_title(f"Ciclo diario — {info['title']}")
        ax.set_xticks(range(0, 24, 3))
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_dir / "ciclos_diarios.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"[OK] {out_dir}/ciclos_diarios.png")


def _plot_ciclo_estacional(series, out_dir):
    """Medias mensuales obs vs mod (ciclo estacional, guia 6.6.3/6.7)."""
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5))
    for ax, var in zip(axes, ["T2", "WS10"]):
        info = series[var]
        cm = info["mod"].groupby(info["mod"].index.month).mean()
        ax.plot(cm.index, cm.values, "b-o", ms=4, label="WRF")
        if info["obs"] is not None:
            co = info["obs"].groupby(info["obs"].index.month).mean()
            ax.plot(co.index, co.values, "r-s", ms=4, label="Obs")
        ax.set_xlabel("Mes")
        ax.set_ylabel(info["units"])
        ax.set_title(f"Ciclo estacional — {info['title']}")
        ax.set_xticks(range(1, 13))
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_dir / "ciclo_estacional.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"[OK] {out_dir}/ciclo_estacional.png")


def _rosa(ax, wd, ws, titulo):
    """Rosa de viento: 16 sectores x clases de velocidad (barras apiladas)."""
    valid = ~(np.isnan(wd) | np.isnan(ws))
    wd, ws = np.asarray(wd)[valid], np.asarray(ws)[valid]
    if len(wd) == 0:
        ax.set_title(f"{titulo} (sin datos)")
        return
    nsec = 16
    ancho = 2 * np.pi / nsec
    centros = np.arange(nsec) * ancho
    clases = [(0, 2), (2, 4), (4, 6), (6, 8), (8, np.inf)]
    colores = plt.cm.viridis(np.linspace(0.15, 0.9, len(clases)))
    sector = (np.round(np.radians(wd) / ancho).astype(int)) % nsec
    base = np.zeros(nsec)
    for (v0, v1), color in zip(clases, colores):
        f = np.array([np.sum((sector == s) & (ws >= v0) & (ws < v1))
                      for s in range(nsec)]) / len(wd) * 100
        etiqueta = f"{v0}-{v1} m/s" if np.isfinite(v1) else f">{v0} m/s"
        ax.bar(centros, f, width=ancho * 0.9, bottom=base, color=color,
               edgecolor="white", linewidth=0.3, label=etiqueta)
        base += f
    ax.set_theta_zero_location("N")
    ax.set_theta_direction(-1)
    ax.set_title(titulo, pad=18)
    ax.legend(fontsize=6, loc="lower right", bbox_to_anchor=(1.15, -0.1))


def _plot_rosas(series, out_dir):
    fig, axes = plt.subplots(1, 2, figsize=(12, 5.5),
                             subplot_kw={"projection": "polar"})
    _rosa(axes[0], series["WD10"]["mod"].values, series["WS10"]["mod"].values,
          "Rosa de viento — WRF")
    if series["WD10"]["obs"] is not None and series["WS10"]["obs"] is not None:
        comun = series["WD10"]["obs"].index.intersection(series["WS10"]["obs"].index)
        _rosa(axes[1], series["WD10"]["obs"].loc[comun].values,
              series["WS10"]["obs"].loc[comun].values, "Rosa de viento — Obs")
    else:
        axes[1].set_title("Rosa de viento — Obs (sin datos)")
    fig.tight_layout()
    fig.savefig(out_dir / "rosas_viento.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"[OK] {out_dir}/rosas_viento.png")


def _plot_mapas_viento(ds, offset, out_dir):
    """Mapas de viento promedio del dominio: diurno/nocturno x verano/invierno
    (guia 6.7: patrones espaciales minimos)."""
    idx = pd.DatetimeIndex(np.asarray(ds["Time"].values))
    local = idx + pd.Timedelta(hours=offset)
    es_dia = np.isin(local.hour, list(HORAS_DIA))
    casos = {
        "Verano - dia":    np.isin(local.month, ESTACIONES["verano"]) & es_dia,
        "Verano - noche":  np.isin(local.month, ESTACIONES["verano"]) & ~es_dia,
        "Invierno - dia":   np.isin(local.month, ESTACIONES["invierno"]) & es_dia,
        "Invierno - noche": np.isin(local.month, ESTACIONES["invierno"]) & ~es_dia,
    }
    xlat = np.asarray(ds["XLAT"].isel(Time=0).values)
    xlon = np.asarray(ds["XLONG"].isel(Time=0).values)
    hgt = np.asarray(ds["HGT"].isel(Time=0).values) if "HGT" in ds else None
    paso = max(1, xlat.shape[0] // 20)

    fig, axes = plt.subplots(2, 2, figsize=(13, 12))
    for ax, (nombre, mask) in zip(axes.flat, casos.items()):
        if not mask.any():
            ax.set_title(f"{nombre} (sin datos)")
            continue
        tsel = np.where(mask)[0]
        u = np.asarray(ds["U10"].isel(Time=tsel).mean("Time").values)
        v = np.asarray(ds["V10"].isel(Time=tsel).mean("Time").values)
        vel = np.sqrt(u ** 2 + v ** 2)
        if hgt is not None:
            ax.contour(xlon, xlat, hgt, levels=10, colors="gray",
                       linewidths=0.4, alpha=0.6)
        pc = ax.pcolormesh(xlon, xlat, vel, cmap="YlOrRd", shading="auto", alpha=0.75)
        ax.quiver(xlon[::paso, ::paso], xlat[::paso, ::paso],
                  u[::paso, ::paso], v[::paso, ::paso], scale=60, width=0.004)
        plt.colorbar(pc, ax=ax, label="m/s", shrink=0.85)
        ax.set_title(f"Viento 10 m — {nombre} (n={mask.sum()})")
        ax.set_xlabel("Lon")
        ax.set_ylabel("Lat")
    fig.suptitle("Patrones espaciales de viento (promedios)", fontsize=14,
                 fontweight="bold")
    fig.tight_layout()
    fig.savefig(out_dir / "mapas_viento.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"[OK] {out_dir}/mapas_viento.png")


def validar_wrf(config_path):
    with open(config_path) as f:
        cfg = yaml.safe_load(f)

    proyecto = cfg["proyecto"]["nombre"].replace(" ", "_")
    etiqueta = cfg["periodo"].get("etiqueta", "")
    val_cfg = cfg.get("validacion", {})
    out_dir = Path(f"data/outputs/{proyecto}_{etiqueta}/validacion")
    out_dir.mkdir(parents=True, exist_ok=True)

    obs_file = Path(val_cfg.get("observaciones", "observaciones.csv"))
    dominio = val_cfg.get("dominio", "d03")
    offset = val_cfg.get("obs_utc_offset", -4)

    wrf_files = sorted(Path("data/wrf").glob(f"wrfout_{dominio}_*"))
    if not wrf_files:
        print(f"[WARN] No se encontraron wrfout_{dominio}_*. Saltando validacion.")
        return

    ds = _open_wrfouts(wrf_files)

    # Excluir spin-up
    ini_eval = cfg["periodo"].get("inicio_evaluacion")
    if ini_eval:
        t0 = datetime.strptime(ini_eval, "%Y-%m-%d_%H:%M:%S")
        ds = ds.sel(Time=slice(t0, None))

    j, i = _punto_estacion(ds, val_cfg)

    # Series puntuales WRF
    u10 = ds["U10"].isel(south_north=j, west_east=i).to_pandas()
    v10 = ds["V10"].isel(south_north=j, west_east=i).to_pandas()
    series = {
        "T2":   {"units": "°C", "title": "Temperatura 2 m (°C)", "obs_col": "t2",
                 "mod": ds["T2"].isel(south_north=j, west_east=i).to_pandas() - 273.15},
        "WS10": {"units": "m/s", "title": "Vel. viento 10 m (m/s)", "obs_col": "ws10",
                 "mod": np.sqrt(u10 ** 2 + v10 ** 2)},
        "WD10": {"units": "deg", "title": "Dir. viento 10 m (deg)", "obs_col": "wd10",
                 "circular": True,
                 "mod": (270.0 - np.degrees(np.arctan2(v10, u10))) % 360.0},
    }

    # Observaciones (hora local -> UTC)
    if obs_file.exists():
        obs_df = pd.read_csv(obs_file, parse_dates=["fecha"]).set_index("fecha")
        if offset:
            obs_df.index = obs_df.index - pd.Timedelta(hours=offset)
            print(f"[INFO] Obs desplazadas {-offset:+d} h a UTC (obs_utc_offset={offset})")
    else:
        print("[WARN] Archivo de observaciones no encontrado. Solo productos WRF.")
        obs_df = pd.DataFrame()

    for var, info in series.items():
        col = info["obs_col"]
        info["obs"] = obs_df[col] if (not obs_df.empty and col in obs_df.columns) else None

    # ── Metricas: globales + por estacion del año + dia/noche (guia 6.8) ────
    filas, metricas_globales = [], {}
    for var, info in series.items():
        if info["obs"] is None:
            continue
        comun = info["mod"].index.intersection(info["obs"].index)
        o = info["obs"].loc[comun]
        m = info["mod"].loc[comun]
        for nombre, mask in _segmentos(comun, offset).items():
            met = compute_metrics(o[mask.values], m[mask.values],
                                  circular=info.get("circular", False))
            filas.append({"variable": var, "segmento": nombre, **met})
            if nombre == "global":
                metricas_globales[var] = met

    if filas:
        df = pd.DataFrame(filas)
        df.to_csv(out_dir / "metricas_validacion.csv", index=False)
        cols = ["variable", "segmento", "n", "sesgo", "r", "rmse", "mae",
                "ioa", "fb", "nmse", "r2"]
        (out_dir / "metricas_validacion.tex").write_text(
            df[cols].to_latex(index=False, float_format="%.2f",
                              caption="Metricas de validacion WRF vs observaciones "
                                      "(globales, por estacion del año y dia/noche)",
                              label="tab:validacion_wrf"))
        print(f"[OK] {out_dir}/metricas_validacion.csv (+ .tex)")

    # ── Graficos (guia 6.6.3 / 6.7) ─────────────────────────────────────────
    _plot_series(series, obs_df, out_dir, proyecto, etiqueta, metricas_globales)
    _plot_ciclos_diarios(series, offset, out_dir)
    _plot_ciclo_estacional(series, out_dir)
    _plot_rosas(series, out_dir)
    _plot_mapas_viento(ds, offset, out_dir)

    ds.close()
    print(f"\n[OK] Post-proceso meteorologico completo en {out_dir}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python3 validar_wrf.py config.yaml")
        sys.exit(1)
    validar_wrf(sys.argv[1])
