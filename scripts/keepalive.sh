# Holds the device in the USB service: a status call every few seconds (reconnects after restarts).
# usage: scripts/keepalive.sh STATEDIR    (stop: touch STATEDIR/stop-keepalive)
S=$(cd "$1" && pwd) || exit 2; cd "$(dirname "$0")/.." || exit 2
# the project's pinned interpreter (uv, through mise when uv is not on PATH), looked up once
PY=$({ command -v uv >/dev/null && uv run --quiet python -c 'import sys; print(sys.executable)'; } 2>/dev/null ||
     mise exec -- uv run --quiet python -c 'import sys; print(sys.executable)') || { echo "keepalive: no project Python (run mise install)"; exit 2; }
echo "== keepalive (stop: touch $S/stop-keepalive) $(date +%T)"
rm -f "$S/stop-keepalive"; fails=0
while [ ! -f "$S/stop-keepalive" ]; do
  if "$PY" xyf_host.py --timeout 60 status > "$S/keepalive.json" 2>&1; then fails=0; else fails=$((fails+1)); echo "keepalive miss $fails $(date +%T)"; [ $fails -ge 3 ] && break; fi
  sleep 2
done
echo "== keepalive ended $(date +%T)"
