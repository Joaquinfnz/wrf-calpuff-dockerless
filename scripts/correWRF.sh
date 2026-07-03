#!/bin/bash
# =============================================================================
# correWRF.sh — Lanza la cadena del servidor (ERA5 -> WPS -> WRF -> CALWRF -> 3D.DAT),
#               desatendido y resiliente para corridas de varios dias.
#               Version DOCKERLESS: binarios nativos en $HOME (sin Docker, sin sudo).
#
# CALWRF corre aqui para reducir el wrfout (~40 GB) a 3D.DAT (~4 GB).
# CALMET -> CALPUFF -> post corren en la PC sobre el 3D.DAT.
#
# Resiliencia ("lo lanzo una vez y no para hasta terminar"):
#   - tmux: sobrevive a desconexiones SSH / cierre del terminal.
#   - bucle de reintento: si un paso se cae, Snakemake relanza la rule y
#     prepare_restart.py reanuda WRF desde el ultimo wrfrst (checkpoint 6h).
#   - corta-circuito: 3 fallos rapidos seguidos (<2 min) = error de config,
#     no transitorio -> aborta.
#
# Uso (en el servidor, dentro del repo, tras scripts/setup_dockerless.sh):
#   export CDSAPI_KEY='<tu-token-CDS>'
#   bash scripts/correWRF.sh
#
# Monitoreo:  tmux attach -t wrf   |   tail -f correwrf.log
# =============================================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WF="$(dirname "$SCRIPT_DIR")"
SELF="$SCRIPT_DIR/correWRF.sh"
cd "$WF"

G='\033[0;32m'; Y='\033[1;33m'; R='\033[0;31m'; N='\033[0m'
info(){ echo -e "${G}[INFO]${N}  $1"; }
warn(){ echo -e "${Y}[WARN]${N}  $1"; }
err(){  echo -e "${R}[ERROR]${N} $1"; }

# Entorno nativo (micromamba: python, snakemake, mpirun, tmux, tcsh + LD_LIBRARY_PATH)
source "$SCRIPT_DIR/env_wrf.sh" || exit 1

TARGET="data/calwrf/3d.dat"   # salida final del servidor (CALWRF: ~4 GB)
SESSION="wrf"

# ── Modo interno: bucle de reintento auto-reanudable (corre dentro de tmux) ──
if [ "${1:-}" = "__loop" ]; then
    CORES=$(nproc)
    fastfail=0
    while [ ! -f "$TARGET" ]; do
        snakemake --unlock >/dev/null 2>&1 || true   # limpiar lock de una caida previa
        t0=$SECONDS
        info "[$(date '+%F %T')] Iniciando/reanudando Snakemake (rule all -> 3D.DAT + validacion)"
        if snakemake --cores "$CORES" --keep-going --rerun-incomplete --latency-wait 120; then
            info "[$(date '+%F %T')] PIPELINE COMPLETO. 3D.DAT (~4 GB) listo en data/calwrf/."
            exit 0
        fi
        dur=$((SECONDS - t0))
        if [ "$dur" -lt 120 ]; then
            fastfail=$((fastfail + 1))
            warn "[$(date '+%F %T')] Fallo rapido (${dur}s) — $fastfail/3"
            if [ "$fastfail" -ge 3 ]; then
                err "3 fallos rapidos seguidos = error de config/setup (no transitorio)."
                err "Abortando. Revisa correwrf.log y data/wrf/rsl.error.0000"
                exit 1
            fi
        else
            fastfail=0
            warn "[$(date '+%F %T')] Interrupcion tras ${dur}s. Reanudando en 60s desde el checkpoint..."
        fi
        sleep 60
    done
    info "[$(date '+%F %T')] $TARGET ya existe. Nada que hacer."
    exit 0
fi

# ── 1. Chequeos de setup ────────────────────────────────────────────────────
[ -f config.yaml ] || { err "Falta config.yaml en $WF"; exit 1; }

# Binarios nativos leidos del config (los compila setup_dockerless.sh)
EXES=$(python3 - <<'EOF'
import os, yaml
e = yaml.safe_load(open("config.yaml"))["ejecucion"]
print(os.path.expanduser(e["wrf_home"]) + "/main/wrf.exe",
      os.path.expanduser(e["wrf_home"]) + "/main/real.exe",
      os.path.expanduser(e["wps_home"]) + "/geogrid.exe",
      os.path.expanduser(e["wps_home"]) + "/ungrib.exe",
      os.path.expanduser(e["wps_home"]) + "/metgrid.exe",
      os.path.expanduser(e["calwrf_exe"]))
EOF
)
for exe in $EXES; do
    [ -x "$exe" ] || { err "Falta el binario $exe. Corre primero: bash scripts/setup_dockerless.sh"; exit 1; }
done

# WPS_GEOG = carpetas de datos de terreno (GEOGRID.TBL es parte de WPS, no va aqui)
WPS_GEOG_DIR=$(python3 -c "import os,yaml;print(os.path.expanduser(yaml.safe_load(open('config.yaml'))['rutas']['wps_geog']))")
[ -d "$WPS_GEOG_DIR" ] && [ -n "$(ls -A "$WPS_GEOG_DIR" 2>/dev/null)" ] || {
    err "WPS_GEOG vacio o inexistente en $WPS_GEOG_DIR. Corre primero: bash scripts/setup_dockerless.sh"; exit 1; }
: "${CDSAPI_KEY:?Define CDSAPI_KEY (token CDS): export CDSAPI_KEY=...}"
command -v tmux >/dev/null 2>&1 || { err "tmux no esta en el entorno (re-corre setup_dockerless.sh)"; exit 1; }
command -v snakemake >/dev/null 2>&1 || {
    info "Instalando snakemake en el entorno..."
    python3 -m pip install -q snakemake
}

CORES=$(nproc)
NP=$(python3 -c "import yaml;print(yaml.safe_load(open('config.yaml'))['ejecucion']['nprocs'])")
info "Cores disponibles: $CORES | WRF mpirun -np: $NP | objetivo: $TARGET"
[ "$NP" -gt "$CORES" ] && warn "nprocs ($NP) > cores ($CORES): se acota a $CORES (ajusta ejecucion.nprocs en config.yaml)"

# ── 2. Validar config contra la Guia SEA (no fatal: permite benchmarks cortos) ──
python3 workflow/scripts/check_config.py config.yaml || warn "check_config con observaciones (revisa arriba)"

# ── 3. No duplicar si ya hay una corrida en curso ──────────────────────────
if tmux has-session -t "$SESSION" 2>/dev/null; then
    warn "Ya existe una sesion '$SESSION'. Adjunta con: tmux attach -t $SESSION"
    exit 0
fi

# ── 4. Lanzar el bucle resiliente dentro de tmux (sobrevive a la desconexion SSH) ──
tmux new-session -d -s "$SESSION" "bash '$SELF' __loop 2>&1 | tee -a correwrf.log"

info "Lanzado en tmux '$SESSION' (corre aunque cierres la sesion SSH)."
info "  Monitorear:  tmux attach -t $SESSION   (salir sin cortar: Ctrl+B luego D)"
info "  Log:         tail -f correwrf.log"
info "  Al terminar: data/calwrf/3d.dat (~4 GB) -> baja a la PC con scripts/sync_wrf.sh"
