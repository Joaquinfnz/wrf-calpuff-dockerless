# WRF + CALWRF sin Docker ni sudo — Meteorología para calidad del aire SEIA

Pipeline que corre **en un servidor Linux donde NO eres administrador**
(universidad, cluster compartido, etc.) y entrega el **`3D.DAT`** de CALWRF
(~4 GB): la meteorología lista para CALMET. Todo se instala **en tu `$HOME`**
con [micromamba](https://mamba.readthedocs.io) (conda-forge) y WRF/WPS/CALWRF
se **compilan desde fuente** — sin `sudo`, sin Docker, sin pedirle nada al admin.

> **Alcance de este repo = solo servidor:** `ERA5 → WPS → WRF → CALWRF → 3D.DAT`.
> En la **PC** sigue (más liviano): `CALMET → CALPUFF → post-proceso SEIA`.
> Es la variante *dockerless* de
> [wrf-calpuff-workflow](https://github.com/Joaquinfnz/wrf-calpuff-workflow)
> (misma cadena, mismos scripts; allá los binarios van en imágenes Docker).

Repo: https://github.com/Joaquinfnz/wrf-calpuff-dockerless

---

## Requisitos

- Linux **x86_64** (probado en Ubuntu 22.04/24.04). Sin privilegios: basta un
  usuario normal con `curl` o `wget`.
- Espacio en disco: ~12 GB (toolchain + compilados) + ~10 GB (WPS_GEOG)
  + ~15–35 GB (ERA5 un año) + ~40 GB (`wrfout` un año) → **~100–120 GB libres**.
  Si tu `$HOME` tiene cuota chica, usa `DATA_DIR` (abajo).
- Token del [CDS](https://cds.climate.copernicus.eu/profile) para descargar ERA5
  (aceptar las licencias de `reanalysis-era5-pressure-levels` y `single-levels`).

## 1. Instalación (una vez, ~1–2 h)

```bash
git clone https://github.com/Joaquinfnz/wrf-calpuff-dockerless.git
cd wrf-calpuff-dockerless
bash scripts/setup_dockerless.sh
```

El setup instala micromamba + un entorno `wrf` (gcc/gfortran 12, MPICH, NetCDF,
HDF5, Python 3.12, Snakemake, tmux, tcsh — todo de conda-forge, en espacio de
usuario) y compila desde fuente **Jasper 1.900.1**, **WRF 4.6.0**, **WPS 4.6.0**
y **CALWRF 2.0.3** en `~/opt/`; además baja el terreno estático WPS_GEOG 30s.
Es re-ejecutable: si se corta, vuelve a lanzarlo y salta lo ya hecho.

Variables opcionales (exportar **antes** del setup):

| Variable | Default | Para qué |
|----------|---------|----------|
| `DATA_DIR` | — | disco grande para los datos de la corrida (symlink `data/`) |
| `OPT_DIR` | `~/opt` | destino de los compilados (ajustar `ejecucion.*` en config) |
| `WPS_GEOG_DIR` | `~/data/WPS_GEOG` | terreno estático (ajustar `rutas.wps_geog`) |

## 2. Configurar el proyecto

```bash
source scripts/env_wrf.sh           # activa el entorno (en cada sesión nueva)
python3 workflow/scripts/importar_kmz.py proyecto.kmz --apply config.yaml
export CDSAPI_KEY='<tu-token>'      # crear en cds.climate.copernicus.eu/profile
```

Revisa fechas y físicas en `config.yaml`; ajusta `ejecucion.nprocs` al número
de cores del servidor (`nproc`). En servidores compartidos sé buen vecino:
no uses todos los cores si hay más gente trabajando.

## 3. Correr (desatendido, resiliente)

```bash
bash scripts/correWRF.sh
```

Lanza en `tmux` la cadena `ERA5 → WPS → WRF → CALWRF` hasta `data/calwrf/3d.dat`,
con **reintento auto-reanudable**: si WRF se cae, `prepare_restart.py` detecta el
último checkpoint `wrfrst_*` (cada 6 h) y parcha el namelist para **reanudar desde
ahí** (no desde cero); si falla 3 veces seguidas rápido, se detiene (error de
config, no transitorio). La descarga ERA5 es por mes (reanudable) e incluye el
spin-up aunque caiga en el año anterior.

```bash
tmux attach -t wrf     # monitorear (salir sin cortar: Ctrl+B, D)
tail -f correwrf.log
```

## 4. Bajar el 3D.DAT a la PC

```bash
bash scripts/sync_wrf.sh usuario@servidor [ruta/al/repo] [llave.pem]
```

Trae `3d.dat` (~4 GB) + namelists + validación. En la PC: CALMET → CALPUFF → post.

---

## Cómo funciona el modo dockerless

| Pieza | Docker (repo original) | Aquí |
|-------|------------------------|------|
| Toolchain (gcc, MPI, NetCDF) | dentro de la imagen | entorno micromamba en `$HOME` |
| WRF / WPS / CALWRF | compilados en el build de la imagen | compilados por `setup_dockerless.sh` en `~/opt/` |
| Ejecución | `docker run …` en cada rule | binarios nativos + `LD_LIBRARY_PATH` (`scripts/env_wrf.sh`) |
| Permisos | requiere daemon Docker (root) | **ninguno** |

Detalles que resuelve el setup: shims `gcc`/`gfortran`/`cpp`/`ar` (los
compiladores de conda-forge vienen con prefijo), `configure.wrf` asume
`/lib/cpp` (se parcha), Jasper 1.900.1 se compila con `-fcommon`, y los
binarios encuentran las librerías vía `env_wrf.sh` (por eso hay que hacer
`source` antes de correr nada a mano).

## Parametrizaciones físicas

Validadas para el sur de Chile (Falvey & Garreaud 2009; Schmitz et al. 2021):
WSM6, Kain-Fritsch (d01/d02), YSU, Revised MM5, Noah LSM, Dudhia/RRTM.

## Estructura del repositorio

```
├── config.yaml              # Dominio, fechas, físicas + rutas de binarios (ejecucion)
├── static/
│   ├── namelist.wps.j2 · namelist.input.j2
│   └── Vtable.ERA5          # tabla ungrib para ERA5 (pl+sfc)
├── workflow/
│   ├── Snakefile            # pipeline hasta 3d.dat (binarios nativos)
│   └── scripts/             # check_config, download_era5, render_namelist,
│                            #   importar_kmz, gen_calwrf_inp, validar_wrf,
│                            #   prepare_restart (reanudar WRF desde wrfrst)
└── scripts/
    ├── setup_dockerless.sh  # instala TODO en $HOME (sin sudo)
    ├── env_wrf.sh           # activa el entorno (source en cada sesión)
    ├── correWRF.sh          # lanza la corrida en tmux, auto-reanudable
    └── sync_wrf.sh          # baja el 3D.DAT a la PC
```

## Licencia

MIT. WRF/WPS y CALWRF son open-source (se compilan desde fuente).
