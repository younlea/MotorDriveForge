#!/bin/bash
# =============================================================================
# MotorDriveForge 시작/중지 스크립트
#
# 사용법:
#   ./start.sh            # 기본 실행 (Qdrant=Docker, Backend/Frontend=Python)
#   ./start.sh docker     # 전체 Docker Compose 모드
#   ./start.sh stop       # 모든 서비스 중지
#   ./start.sh status     # 서비스 상태 확인
#   ./start.sh restart    # 재시작
#
# 환경변수 (선택):
#   BACKEND_PORT=8000       백엔드 포트 (기본 8000)
#   FRONTEND_PORT=8501      프론트엔드 포트 (기본 8501)
#   OLLAMA_URL=http://...   Ollama 주소 (기본 http://localhost:11434)
#   CUBEMX_PATH=/path/...   CubeMX 실행 파일 경로 (선택)
# =============================================================================

set -euo pipefail

# ── 색상 ────────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; CYAN='\033[0;36m'; NC='\033[0m'
ok()   { echo -e "${GREEN}[✓]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
err()  { echo -e "${RED}[✗]${NC} $*"; }
info() { echo -e "${CYAN}[·]${NC} $*"; }

# ── 설정 ────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-8501}"
OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"

PID_DIR="/tmp/mdf_pids"
LOG_DIR="/tmp/mdf_logs"
mkdir -p "$PID_DIR" "$LOG_DIR"

MODE="${1:-dev}"

# ── 헬퍼 ────────────────────────────────────────────────────────────────────

wait_for_port() {
    local name="$1" port="$2" timeout="${3:-30}"
    local i=0
    while ! bash -c ":> /dev/tcp/127.0.0.1/$port" 2>/dev/null; do
        i=$((i+1))
        if [ $i -ge $timeout ]; then
            err "$name 시작 실패 (포트 $port 응답 없음)"
            return 1
        fi
        sleep 1
    done
    ok "$name 준비 완료 (포트 $port)"
}

stop_pid_file() {
    local file="$PID_DIR/$1.pid"
    if [ -f "$file" ]; then
        local pid
        pid=$(cat "$file")
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null && info "$1 중지 (PID $pid)"
        fi
        rm -f "$file"
    fi
}

is_running() {
    local file="$PID_DIR/$1.pid"
    [ -f "$file" ] && kill -0 "$(cat "$file")" 2>/dev/null
}

# ── STATUS ───────────────────────────────────────────────────────────────────

cmd_status() {
    echo ""
    echo "════════════════════════════════════════"
    echo "  MotorDriveForge 서비스 상태"
    echo "════════════════════════════════════════"

    # Qdrant
    if bash -c ":> /dev/tcp/127.0.0.1/6333" 2>/dev/null; then
        ok "Qdrant       http://localhost:6333"
    else
        err "Qdrant       오프라인"
    fi

    # Backend
    if bash -c ":> /dev/tcp/127.0.0.1/$BACKEND_PORT" 2>/dev/null; then
        ok "Backend      http://localhost:$BACKEND_PORT"
        ok "  API Docs   http://localhost:$BACKEND_PORT/docs"
    else
        err "Backend      오프라인 (포트 $BACKEND_PORT)"
    fi

    # Frontend
    if bash -c ":> /dev/tcp/127.0.0.1/$FRONTEND_PORT" 2>/dev/null; then
        ok "Frontend     http://localhost:$FRONTEND_PORT"
    else
        err "Frontend     오프라인 (포트 $FRONTEND_PORT)"
    fi

    # Ollama
    if curl -s --max-time 2 "$OLLAMA_URL/api/tags" > /dev/null 2>&1; then
        local models
        models=$(curl -s "$OLLAMA_URL/api/tags" | python3 -c "import sys,json; d=json.load(sys.stdin); print(', '.join(m['name'] for m in d.get('models',[])[:3]))" 2>/dev/null || echo "?")
        ok "Ollama       $OLLAMA_URL  [$models]"
    else
        warn "Ollama       응답 없음 ($OLLAMA_URL)"
    fi

    # 외부 IP
    local ext_ip
    ext_ip=$(hostname -I | awk '{print $1}')
    echo ""
    info "로컬 네트워크 접속: http://$ext_ip:$FRONTEND_PORT"
    echo ""
}

# ── STOP ─────────────────────────────────────────────────────────────────────

cmd_stop() {
    info "서비스 중지 중..."

    if [ -f "$SCRIPT_DIR/docker-compose.yml" ]; then
        docker compose -f "$SCRIPT_DIR/docker-compose.yml" down --remove-orphans 2>/dev/null || true
    fi

    stop_pid_file backend
    stop_pid_file frontend

    # 혹시 남은 프로세스 정리
    pkill -f "uvicorn backend.main" 2>/dev/null || true
    pkill -f "streamlit run frontend" 2>/dev/null || true

    ok "모든 서비스 중지 완료"
}

# ── DOCKER 모드 ───────────────────────────────────────────────────────────────

cmd_docker() {
    echo ""
    echo "════════════════════════════════════════"
    echo "  MotorDriveForge — Docker Compose 모드"
    echo "════════════════════════════════════════"

    # 전제 조건
    if ! command -v docker &>/dev/null; then
        err "Docker가 설치되어 있지 않습니다."; exit 1
    fi

    info "Docker Compose 빌드 및 시작..."
    OLLAMA_URL="$OLLAMA_URL" docker compose up -d --build 2>&1 | tail -5

    echo ""
    info "서비스 준비 대기 중..."
    wait_for_port "Qdrant"   6333 30
    wait_for_port "Backend"  "$BACKEND_PORT" 60
    wait_for_port "Frontend" "$FRONTEND_PORT" 60

    cmd_status
}

# ── DEV 모드 (기본) ───────────────────────────────────────────────────────────

cmd_dev() {
    echo ""
    echo "════════════════════════════════════════"
    echo "  MotorDriveForge — Dev 모드 시작"
    echo "  (Qdrant=Docker, Backend/Frontend=Python)"
    echo "════════════════════════════════════════"

    # 전제 조건 확인
    if ! command -v python3 &>/dev/null; then
        err "python3가 설치되어 있지 않습니다."; exit 1
    fi
    if ! command -v docker &>/dev/null; then
        err "Docker가 설치되어 있지 않습니다."; exit 1
    fi

    # ── 1. Qdrant ────────────────────────────────────────────────────────────
    info "Qdrant 시작..."
    if bash -c ":> /dev/tcp/127.0.0.1/6333" 2>/dev/null; then
        ok "Qdrant 이미 실행 중"
    else
        if docker ps -a --format '{{.Names}}' | grep -q "^stm32_qdrant$"; then
            docker start stm32_qdrant > /dev/null
        else
            docker compose -f "$SCRIPT_DIR/docker-compose.yml" up -d qdrant > /dev/null 2>&1
        fi
        wait_for_port "Qdrant" 6333 30
    fi

    # ── 2. 패키지 확인 ───────────────────────────────────────────────────────
    info "Python 패키지 확인..."
    if ! python3 -c "import fastapi, streamlit, uvicorn" 2>/dev/null; then
        warn "패키지 설치 중..."
        pip install -q -r "$SCRIPT_DIR/backend/requirements.txt"
        pip install -q -r "$SCRIPT_DIR/frontend/requirements.txt"
    fi

    # ── 3. Backend ───────────────────────────────────────────────────────────
    info "Backend 시작 (포트 $BACKEND_PORT)..."
    stop_pid_file backend 2>/dev/null || true

    OLLAMA_URL="$OLLAMA_URL" \
    BACKEND_URL="http://localhost:$BACKEND_PORT" \
    python3 -m uvicorn backend.main:app \
        --host 0.0.0.0 \
        --port "$BACKEND_PORT" \
        > "$LOG_DIR/backend.log" 2>&1 &
    echo $! > "$PID_DIR/backend.pid"

    wait_for_port "Backend" "$BACKEND_PORT" 30

    # ── 4. Frontend ──────────────────────────────────────────────────────────
    info "Frontend 시작 (포트 $FRONTEND_PORT)..."
    stop_pid_file frontend 2>/dev/null || true

    BACKEND_URL="http://localhost:$BACKEND_PORT" \
    python3 -m streamlit run frontend/app.py \
        --server.port "$FRONTEND_PORT" \
        --server.address 0.0.0.0 \
        > "$LOG_DIR/frontend.log" 2>&1 &
    echo $! > "$PID_DIR/frontend.pid"

    wait_for_port "Frontend" "$FRONTEND_PORT" 30

    cmd_status

    echo ""
    echo "  로그 확인:"
    info "  Backend  → tail -f $LOG_DIR/backend.log"
    info "  Frontend → tail -f $LOG_DIR/frontend.log"
    echo ""
    echo "  종료: ./start.sh stop"
    echo ""
}

# ── MAIN ─────────────────────────────────────────────────────────────────────

case "$MODE" in
    docker)   cmd_docker ;;
    stop)     cmd_stop   ;;
    status)   cmd_status ;;
    restart)  cmd_stop; sleep 2; cmd_dev ;;
    dev|"")   cmd_dev    ;;
    *)
        echo "사용법: $0 [dev|docker|stop|status|restart]"
        exit 1
        ;;
esac
