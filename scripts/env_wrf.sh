# =============================================================================
# env_wrf.sh — Activa el entorno nativo (micromamba) para correr el workflow.
#
# Uso:  source scripts/env_wrf.sh     (en cada sesion, antes de correWRF.sh;
#                                      correWRF.sh tambien lo hace solo)
# Respeta las mismas variables del setup: OPT_DIR, MAMBA_ROOT_PREFIX,
# MICROMAMBA_BIN. Debe coincidir con lo instalado por setup_dockerless.sh.
# =============================================================================

export MAMBA_ROOT_PREFIX="${MAMBA_ROOT_PREFIX:-$HOME/micromamba}"
_MM="${MICROMAMBA_BIN:-$HOME/.local/bin/micromamba}"
_OPT="${OPT_DIR:-$HOME/opt}"

if [ ! -x "$_MM" ]; then
    echo "[ERROR] micromamba no encontrado en $_MM — corre primero: bash scripts/setup_dockerless.sh" >&2
    return 1 2>/dev/null || exit 1
fi

# El hook de micromamba y 'activate' referencian variables (PS1, etc.) sin
# definir; bajo el 'set -u' de correWRF.sh eso aborta. Se relaja 'set -u' solo
# aqui (se restaura despues) y se asegura PS1.
_had_u=0; case "$-" in *u*) _had_u=1;; esac
set +u
: "${PS1:=}"
eval "$("$_MM" shell hook --shell bash)"
micromamba activate wrf || {
    echo "[ERROR] no existe el entorno 'wrf' — corre primero: bash scripts/setup_dockerless.sh" >&2
    [ "$_had_u" = 1 ] && set -u
    return 1 2>/dev/null || exit 1
}
[ "$_had_u" = 1 ] && set -u

# Los binarios compilados (WRF/WPS) buscan libnetcdf/libjasper en runtime
export LD_LIBRARY_PATH="$CONDA_PREFIX/lib:$_OPT/jasper/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PATH="$_OPT/shims:$PATH"

# Binario dm+sm: 1 hilo OpenMP por rank MPI (el paralelismo lo pone mpirun)
export OMP_NUM_THREADS=1
# WRF necesita stack grande; en servidores compartidos puede estar capado (no fatal)
ulimit -s unlimited 2>/dev/null || true
