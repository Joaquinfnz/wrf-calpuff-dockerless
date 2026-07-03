#!/bin/bash
# =============================================================================
# setup_dockerless.sh — Instalacion SIN sudo y SIN Docker (100% en $HOME).
#
# Para servidores compartidos (universidad, cluster, etc.) donde el usuario NO
# es administrador: todo el toolchain se instala en espacio de usuario con
# micromamba (conda-forge) y WRF/WPS/CALWRF se compilan desde fuente.
#
# Uso:
#   1. Conectate por SSH al servidor (Linux x86_64)
#   2. git clone https://github.com/Joaquinfnz/wrf-calpuff-dockerless.git
#   3. cd wrf-calpuff-dockerless && bash scripts/setup_dockerless.sh
#
# Instala / compila:
#   - micromamba + entorno "wrf": gcc/gfortran 12, MPICH, NetCDF, HDF5,
#     Python 3.12, Snakemake, tmux, tcsh   (todo de conda-forge, sin root)
#   - Jasper 1.900.1 (fuente)  -> $OPT_DIR/jasper    (GRIB2 para ungrib)
#   - WRF 4.6.0 (fuente)       -> $OPT_DIR/wrf
#   - WPS 4.6.0 (fuente)       -> $OPT_DIR/wps
#   - CALWRF 2.0.3 (fuente)    -> $OPT_DIR/calwrf/calwrf.exe
#   - WPS_GEOG 30s (~2.6 GB descarga / ~10 GB en disco) -> $WPS_GEOG_DIR
#
# Variables opcionales (export antes de correr):
#   OPT_DIR       destino de los compilados     (default: $HOME/opt)
#   WPS_GEOG_DIR  datos estaticos de terreno    (default: $HOME/data/WPS_GEOG)
#   DATA_DIR      si el home tiene cuota chica: ruta en un disco grande para
#                 los datos de la corrida (crea el symlink data/ -> $DATA_DIR)
#   * Si cambias OPT_DIR o WPS_GEOG_DIR, ajusta 'ejecucion' y 'rutas.wps_geog'
#     en config.yaml para que apunten a lo mismo.
#
# Es re-ejecutable: los pasos ya completados se saltan.
# Tiempo estimado: 1 - 2 horas (mayormente compilando WRF).
# =============================================================================

set -eo pipefail

OPT_DIR="${OPT_DIR:-$HOME/opt}"
WPS_GEOG_DIR="${WPS_GEOG_DIR:-$HOME/data/WPS_GEOG}"
MAMBA_ROOT_PREFIX="${MAMBA_ROOT_PREFIX:-$HOME/micromamba}"
MICROMAMBA_BIN="${MICROMAMBA_BIN:-$HOME/.local/bin/micromamba}"
ENV_NAME="wrf"
WRF_VERSION="4.6.0"
WPS_VERSION="4.6.0"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKFLOW_DIR="$(dirname "$SCRIPT_DIR")"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
log_info()  { echo -e "${GREEN}[INFO]${NC}  $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_step()  { echo -e "\n${CYAN}━━━ $1 ━━━${NC}\n"; }

# ── Verificaciones previas ───────────────────────────────────────────────────
log_step "Verificando sistema"

[ "$(uname -s)" = "Linux" ] || { log_error "Este setup es para Linux (detectado: $(uname -s))."; exit 1; }
[ "$(uname -m)" = "x86_64" ] || { log_error "Se requiere x86_64 (detectado: $(uname -m))."; exit 1; }
if [ -f /etc/os-release ]; then . /etc/os-release; log_info "OS: ${NAME:-?} ${VERSION_ID:-?} (no se necesita sudo)"; fi

command -v curl >/dev/null 2>&1 || command -v wget >/dev/null 2>&1 || {
    log_error "Se necesita 'curl' o 'wget' en el sistema para el bootstrap inicial."; exit 1; }

# Espacio: toolchain+compilados ~12 GB, WPS_GEOG ~10 GB, ERA5 ~15-35 GB,
# wrfout de un anio ~40 GB -> recomendado >= 120 GB libres.
FREE_GB=$(df -Pk "$HOME" | awk 'NR==2 {printf "%d", $4/1024/1024}')
if [ "${FREE_GB:-0}" -lt 120 ]; then
    log_warn "Solo ${FREE_GB} GB libres en \$HOME. Una corrida de anio completo necesita ~100-120 GB."
    log_warn "Si hay un disco de datos grande, usa: export DATA_DIR=/ruta/grande y re-ejecuta."
fi

descargar() {  # descargar <url> <destino>
    if command -v wget >/dev/null 2>&1; then wget -c -q --show-progress "$1" -O "$2"
    else curl -L -C - -o "$2" "$1"; fi
}

# ── 1. micromamba (binario estatico, sin root) ──────────────────────────────
log_step "Paso 1/8: micromamba"

if [ -x "$MICROMAMBA_BIN" ]; then
    log_info "micromamba ya instalado: $MICROMAMBA_BIN"
else
    mkdir -p "$(dirname "$MICROMAMBA_BIN")"
    descargar "https://github.com/mamba-org/micromamba-releases/releases/latest/download/micromamba-linux-64" "$MICROMAMBA_BIN"
    chmod +x "$MICROMAMBA_BIN"
    log_info "micromamba instalado en $MICROMAMBA_BIN"
fi
export MAMBA_ROOT_PREFIX

# ── 2. Entorno conda-forge con todo el toolchain ────────────────────────────
log_step "Paso 2/8: Entorno '$ENV_NAME' (compiladores + MPI + NetCDF + Python)"

if "$MICROMAMBA_BIN" env list 2>/dev/null | grep -q "envs/$ENV_NAME"; then
    log_info "Entorno '$ENV_NAME' ya existe, se reutiliza"
else
    # gcc/gfortran 12: combinacion probada con WRF 4.6 (gfortran >= 14 rompe
    # codigo legado). MPICH corre en espacio de usuario (sin root).
    "$MICROMAMBA_BIN" create -y -n "$ENV_NAME" -c conda-forge --override-channels \
        python=3.12 pip \
        gcc_linux-64=12 gxx_linux-64=12 gfortran_linux-64=12 \
        mpich \
        netcdf-fortran hdf5 \
        libpng zlib \
        make m4 perl tcsh tmux git wget unzip rsync bzip2
    log_info "Entorno '$ENV_NAME' creado"
fi

eval "$("$MICROMAMBA_BIN" shell hook --shell bash)"
micromamba activate "$ENV_NAME"
log_info "Entorno activo: $CONDA_PREFIX"

# ── 3. Shims de compiladores ────────────────────────────────────────────────
# WRF/WPS/Jasper invocan 'gcc', 'gfortran', 'cpp', 'ar'... a secas; los
# binarios de conda-forge vienen con prefijo x86_64-conda-linux-gnu-*.
log_step "Paso 3/8: Shims de compiladores"

SHIMS="$OPT_DIR/shims"
mkdir -p "$SHIMS"
shim() {  # shim <nombre> <destino>
    [ -n "$2" ] && [ -x "$2" ] && ln -sf "$2" "$SHIMS/$1"
}
shim gcc      "${CC:-}"
shim g++      "${CXX:-}"
shim gfortran "${FC:-}"
shim cpp      "$CONDA_PREFIX/bin/x86_64-conda-linux-gnu-cpp"
shim ar       "$CONDA_PREFIX/bin/x86_64-conda-linux-gnu-ar"
shim ranlib   "$CONDA_PREFIX/bin/x86_64-conda-linux-gnu-ranlib"
shim nm       "$CONDA_PREFIX/bin/x86_64-conda-linux-gnu-nm"
shim ld       "$CONDA_PREFIX/bin/x86_64-conda-linux-gnu-ld"
if [ ! -x "$SHIMS/cpp" ]; then  # fallback: cpp via gcc -E
    printf '#!/bin/bash\nexec "%s" -E "$@"\n' "${CC:?}" > "$SHIMS/cpp" && chmod +x "$SHIMS/cpp"
fi
for tool in gcc gfortran cpp ar; do
    [ -x "$SHIMS/$tool" ] || { log_error "No se pudo crear el shim '$tool' (¿fallo el entorno conda?)"; exit 1; }
done
export PATH="$SHIMS:$PATH"
log_info "Shims en $SHIMS ($(gcc -dumpversion 2>/dev/null || echo '?'))"

# ── Variables de compilacion (mismas que usaba la imagen Docker) ────────────
export NETCDF="$CONDA_PREFIX"
export HDF5="$CONDA_PREFIX"
export JASPERLIB="$OPT_DIR/jasper/lib"
export JASPERINC="$OPT_DIR/jasper/include"
export LD_LIBRARY_PATH="$CONDA_PREFIX/lib:$JASPERLIB${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export WRF_EM_CORE=1 WRF_NMM_CORE=0 WRF_CHEM=0 WRF_KPP=0
export WRFIO_NCD_LARGE_FILE_SUPPORT=1
export WRF_DIR="$OPT_DIR/wrf"
NPROC_BUILD=$(nproc)

# ── 4. Jasper 1.900.1 (GRIB2/JPEG2000 para ungrib; fuente, como en Docker) ──
log_step "Paso 4/8: Jasper 1.900.1"

if [ -f "$JASPERLIB/libjasper.a" ] || [ -f "$JASPERLIB/libjasper.so" ]; then
    log_info "Jasper ya compilado en $OPT_DIR/jasper"
else
    mkdir -p "$OPT_DIR" && cd "$OPT_DIR"
    descargar "https://www2.mmm.ucar.edu/wrf/OnLineTutorial/compile_tutorial/tar_files/jasper-1.900.1.tar.gz" jasper.tar.gz
    tar xzf jasper.tar.gz && cd jasper-1.900.1
    # -fcommon: requerido para compilar este codigo antiguo con gcc >= 10
    CFLAGS="-fcommon" ./configure --prefix="$OPT_DIR/jasper" >/dev/null
    make -j"$NPROC_BUILD" >/dev/null && make install >/dev/null
    cd "$OPT_DIR" && rm -rf jasper-1.900.1 jasper.tar.gz
    log_info "Jasper instalado en $OPT_DIR/jasper"
fi

# ── 5. WRF 4.6.0 (fuente; ~30-60 min) ───────────────────────────────────────
log_step "Paso 5/8: WRF $WRF_VERSION (compilacion larga, ~30-60 min)"

if [ -x "$OPT_DIR/wrf/main/wrf.exe" ] && [ -x "$OPT_DIR/wrf/main/real.exe" ]; then
    log_info "WRF ya compilado en $OPT_DIR/wrf"
else
    cd "$OPT_DIR"
    # Con submodulos: NoahMP es un git submodule (el tarball de GitHub no lo trae)
    [ -d wrf ] || git clone --depth 1 --recurse-submodules --shallow-submodules \
        --branch "v$WRF_VERSION" https://github.com/wrf-model/WRF.git wrf
    cd wrf
    cp -n share/landread.c.dist share/landread.c 2>/dev/null || true
    # 35 = GNU (gfortran/gcc) dm+sm | 1 = anidamiento basico
    printf '35\n1\n' | ./configure
    # configure.wrf asume /lib/cpp (no existe sin root): usar el cpp del shim
    sed -i 's|/lib/cpp|cpp|g' configure.wrf
    tcsh ./compile -j "$NPROC_BUILD" em_real 2>&1 | tee compile.log | tail -20
    [ -x main/wrf.exe ] && [ -x main/real.exe ] || {
        log_error "Fallo la compilacion de WRF. Revisa $OPT_DIR/wrf/compile.log"; exit 1; }
    log_info "WRF compilado: $OPT_DIR/wrf/main/{wrf.exe,real.exe}"
fi

# ── 6. WPS 4.6.0 (fuente) ───────────────────────────────────────────────────
log_step "Paso 6/8: WPS $WPS_VERSION"

if [ -x "$OPT_DIR/wps/geogrid.exe" ] && [ -x "$OPT_DIR/wps/ungrib.exe" ] && [ -x "$OPT_DIR/wps/metgrid.exe" ]; then
    log_info "WPS ya compilado en $OPT_DIR/wps"
else
    cd "$OPT_DIR"
    if [ ! -d wps ]; then
        descargar "https://github.com/wrf-model/WPS/archive/refs/tags/v$WPS_VERSION.tar.gz" wps.tar.gz
        tar xzf wps.tar.gz && mv "WPS-$WPS_VERSION" wps && rm wps.tar.gz
    fi
    cd wps
    # 3 = Linux x86_64 gfortran (dmpar); WRF_DIR/JASPER* ya exportados
    printf '3\n' | ./configure
    # mismos ajustes que la imagen Docker + rutas conda para png/z en el link
    sed -i '/^LDFLAGS/ s|=.*|= -fopenmp|' configure.wps
    sed -i 's|/usr/bin/cpp|cpp|g; s|/lib/cpp|cpp|g' configure.wps
    sed -i "s|^COMPRESSION_LIBS.*|COMPRESSION_LIBS = -L$JASPERLIB -L$CONDA_PREFIX/lib -Wl,-rpath,$JASPERLIB -Wl,-rpath,$CONDA_PREFIX/lib -ljasper -lpng -lz|" configure.wps
    tcsh ./compile 2>&1 | tee compile.log | tail -10
    [ -x geogrid.exe ] && [ -x ungrib.exe ] && [ -x metgrid.exe ] || {
        log_error "Fallo la compilacion de WPS. Revisa $OPT_DIR/wps/compile.log"; exit 1; }
    log_info "WPS compilado: geogrid.exe, ungrib.exe, metgrid.exe"
fi

# ── 7. CALWRF 2.0.3 (fuente FORTRAN de calpuff.org) ─────────────────────────
log_step "Paso 7/8: CALWRF 2.0.3"

if [ -x "$OPT_DIR/calwrf/calwrf.exe" ]; then
    log_info "CALWRF ya compilado en $OPT_DIR/calwrf"
else
    mkdir -p "$OPT_DIR/calwrf" && cd "$OPT_DIR/calwrf"
    descargar "http://www.calpuff.org/calpuff/download/Mod7_Files/CALWRF_v2.0.3_L190426.zip" calwrf.zip
    unzip -oq calwrf.zip -d calwrf_src && rm calwrf.zip
    SRC="$(find calwrf_src \( -iname 'calwrf*.f' -o -iname 'calwrf*.f90' \) | head -1)"
    [ -n "$SRC" ] || { log_error "No se encontro el fuente de CALWRF en el zip."; exit 1; }
    # flags estandar para FORTRAN legado con gfortran moderno; rpath para que
    # el binario encuentre libnetcdf sin LD_LIBRARY_PATH
    gfortran -O2 -fno-automatic -std=legacy -fallow-argument-mismatch -ffixed-line-length-none \
        -o "$OPT_DIR/calwrf/calwrf.exe" "$SRC" \
        -I"$CONDA_PREFIX/include" -L"$CONDA_PREFIX/lib" -lnetcdff -lnetcdf \
        -Wl,-rpath,"$CONDA_PREFIX/lib"
    rm -rf calwrf_src
    log_info "CALWRF compilado: $OPT_DIR/calwrf/calwrf.exe"
fi

# ── 8. WPS_GEOG (terreno estatico 30s) + Python + directorios ───────────────
log_step "Paso 8/8: WPS_GEOG + dependencias Python + directorios de datos"

# Alta resolucion (30s) — obligatorio para 1 km en terreno complejo
if [ -d "$WPS_GEOG_DIR" ] && [ -n "$(ls -A "$WPS_GEOG_DIR" 2>/dev/null)" ]; then
    log_info "WPS_GEOG ya existe en $WPS_GEOG_DIR"
else
    mkdir -p "$WPS_GEOG_DIR"
    log_info "Descargando WPS_GEOG 30s (~2.6 GB; se descomprime a ~10 GB)..."
    descargar "https://www2.mmm.ucar.edu/wrf/src/wps_files/geog_high_res_mandatory.tar.gz" \
        "$HOME/geog_highres.tar.gz"
    tar -xzf "$HOME/geog_highres.tar.gz" -C "$WPS_GEOG_DIR" --strip-components=1
    rm -f "$HOME/geog_highres.tar.gz"
    log_info "WPS_GEOG extraido en $WPS_GEOG_DIR"
fi

log_info "Instalando dependencias Python (requirements.txt, incluye Snakemake)..."
python3 -m pip install -q -r "$WORKFLOW_DIR/requirements.txt"

cd "$WORKFLOW_DIR"
if [ -n "${DATA_DIR:-}" ]; then
    mkdir -p "$DATA_DIR"
    ln -sfn "$DATA_DIR" data
    log_info "data/ -> $DATA_DIR (symlink por DATA_DIR)"
fi
mkdir -p data/raw data/wps data/wrf data/calwrf data/outputs

# ── Avisos de coherencia con config.yaml ─────────────────────────────────────
[ "$OPT_DIR" = "$HOME/opt" ] || log_warn "OPT_DIR=$OPT_DIR: ajusta 'ejecucion.*' en config.yaml"
[ "$WPS_GEOG_DIR" = "$HOME/data/WPS_GEOG" ] || log_warn "WPS_GEOG_DIR=$WPS_GEOG_DIR: ajusta 'rutas.wps_geog' en config.yaml"

# ── Resumen ──────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo ""
echo "   SETUP COMPLETO — WRF + WPS + CALWRF sin Docker ni sudo"
echo ""
echo "   Binarios:"
echo "     $OPT_DIR/wrf/main/wrf.exe"
echo "     $OPT_DIR/wps/{geogrid,ungrib,metgrid}.exe"
echo "     $OPT_DIR/calwrf/calwrf.exe"
echo ""
echo "   Siguiente paso:"
echo "     1. source scripts/env_wrf.sh      (activa el entorno en cada sesion)"
echo "     2. python3 workflow/scripts/importar_kmz.py proyecto.kmz --apply config.yaml"
echo "     3. export CDSAPI_KEY=...          (token del CDS)"
echo "     4. bash scripts/correWRF.sh       (corre hasta 3D.DAT, en tmux)"
echo ""
