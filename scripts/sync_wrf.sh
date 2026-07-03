#!/bin/bash
# =============================================================================
# sync_wrf.sh — Baja el resultado del servidor (3D.DAT de CALWRF) a la PC.
#
# El servidor reduce el wrfout (~40 GB) a un 3D.DAT (~4 GB) con CALWRF; esto
# baja ese 3D.DAT + namelists + validacion. En la PC sigue: CALMET -> CALPUFF.
#
# Uso (desde la PC):
#   bash sync_wrf.sh ubuntu@<IP> [/ruta/workflow] [llave.pem]
# =============================================================================
set -euo pipefail

SERVER="${1:-}"
REMOTE_DIR="${2:-wrf-calpuff-dockerless}"   # relativo al home del usuario remoto
PEM="${3:-}"

if [ -z "$SERVER" ]; then
    echo "Uso: bash sync_wrf.sh user@server [/ruta/workflow] [llave.pem]"
    exit 1
fi

RSH=""; SCP="scp"
if [ -n "$PEM" ]; then RSH="ssh -i $PEM"; SCP="scp -i $PEM"; fi

LOCAL_DIR="./calwrf_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$LOCAL_DIR"
echo "[INFO] Bajando 3D.DAT (~4 GB) desde $SERVER:$REMOTE_DIR/data/calwrf/"

# 3D.DAT (resultado para CALMET) — rsync reanudable
if [ -n "$PEM" ]; then
    rsync -avz --partial --progress -e "$RSH" "$SERVER:$REMOTE_DIR/data/calwrf/3d.dat" "$LOCAL_DIR/"
else
    rsync -avz --partial --progress "$SERVER:$REMOTE_DIR/data/calwrf/3d.dat" "$LOCAL_DIR/"
fi

# Namelists + validacion (para el expediente SEA)
$SCP "$SERVER:$REMOTE_DIR/data/wrf/namelist.input" "$LOCAL_DIR/" 2>/dev/null || true
$SCP "$SERVER:$REMOTE_DIR/data/wps/namelist.wps"   "$LOCAL_DIR/" 2>/dev/null || true
$SCP "$SERVER:$REMOTE_DIR/data/calwrf/calwrf.log"  "$LOCAL_DIR/" 2>/dev/null || true
$SCP -r "$SERVER:$REMOTE_DIR/data/outputs/"*/validacion "$LOCAL_DIR/validacion" 2>/dev/null || true

echo "[OK] Descargado en $LOCAL_DIR"
echo "     Siguiente en la PC: CALMET (3d.dat) -> CALPUFF -> post."
