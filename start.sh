#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

echo ""
echo -e "${BOLD}${CYAN}  Docket — Email Operations Platform${NC}"
echo -e "  ─────────────────────────────────────"

# ── Kill anything already on our ports ───────────────────────────────────────
for PORT in 8010 5173; do
  PIDS=$(lsof -ti:"$PORT" 2>/dev/null || true)
  if [ -n "$PIDS" ]; then
    echo -e "  ${YELLOW}⚡ Clearing port $PORT...${NC}"
    echo "$PIDS" | xargs kill -9 2>/dev/null || true
    sleep 1
  fi
done

# ── Start backend ─────────────────────────────────────────────────────────────
echo -e "  ${CYAN}▶ Starting backend  (port 8010)...${NC}"
"$DIR/.venv/bin/python" -m uvicorn backend.app.main:app \
  --host 127.0.0.1 --port 8010 --reload \
  > /tmp/docket_backend.log 2>&1 &
BACKEND_PID=$!

# ── Wait for backend to be ready ─────────────────────────────────────────────
echo -n "    Waiting for API"
for i in $(seq 1 20); do
  sleep 1
  if curl -sf http://127.0.0.1:8010/api/v1/system/readiness > /tmp/bank_ready.json 2>/dev/null; then
    STATUS=$(python3 -c "import json; d=json.load(open('/tmp/bank_ready.json')); print(d.get('status','?'))")
    echo -e "\r  ${GREEN}✓ Backend ready${NC}  (status: $STATUS, pid: $BACKEND_PID)          "
    break
  fi
  echo -n "."
  if [ "$i" -eq 20 ]; then
    echo ""
    echo -e "  ${RED}✗ Backend failed to start. Check /tmp/docket_backend.log${NC}"
    cat /tmp/docket_backend.log | tail -15
    exit 1
  fi
done

# ── Start frontend ────────────────────────────────────────────────────────────
echo -e "  ${CYAN}▶ Starting frontend (port 5173)...${NC}"
cd "$DIR/frontend"
npm run dev > /tmp/docket_frontend.log 2>&1 &
FRONTEND_PID=$!
cd "$DIR"

sleep 2
if ! kill -0 "$FRONTEND_PID" 2>/dev/null; then
  echo -e "  ${RED}✗ Frontend failed to start. Check /tmp/docket_frontend.log${NC}"
  cat /tmp/docket_frontend.log | tail -10
  exit 1
fi
echo -e "  ${GREEN}✓ Frontend ready${NC}  (pid: $FRONTEND_PID)"

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo -e "  ─────────────────────────────────────"
echo -e "  ${GREEN}${BOLD}All systems go.${NC}"
echo ""
echo -e "  ${BOLD}Frontend${NC}  →  http://127.0.0.1:5173"
echo -e "  ${BOLD}API docs${NC}  →  http://127.0.0.1:8010/docs"
echo ""
echo -e "  Logs: /tmp/docket_backend.log   /tmp/docket_frontend.log"
echo -e "  ${YELLOW}Press Ctrl+C to shut everything down.${NC}"
echo ""

# ── Open browser ──────────────────────────────────────────────────────────────
sleep 1
open "http://127.0.0.1:5173" 2>/dev/null || true

# ── Keep alive — Ctrl+C kills both ───────────────────────────────────────────
trap "echo ''; echo -e '  ${RED}Shutting down...${NC}'; kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit 0" INT TERM
wait
