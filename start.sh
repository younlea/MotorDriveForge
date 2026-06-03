#!/bin/bash
# =============================================================================
# MotorDriveForge 시작/중지 스크립트
#
# 사용법:
#   ./start.sh            # Docker 모드로 전체 시작 (기본)
#   ./start.sh stop       # 모든 컨테이너 중지
#   ./start.sh restart    # 재시작
#   ./start.sh status     # 서비스 상태 확인
#   ./start.sh logs       # 전체 로그 스트리밍
#   ./start.sh dev        # 개발 모드 (Qdrant=Docker, Backend/Frontend=Python)
#
# 환경변수 (선택):
#   OLLAMA_URL=http://...   Ollama 주소 (기본: http://host.docker.internal:11434)
#   CUBEMX_PATH=/path/...   CubeMX 실행 파일 경로
#   FRONTEND_PORT=8501      프론트엔드 포트
#   BACKEND_PORT=8000       백엔드 포트
# =============================================================================

set -euo pipefail

# ── 색상 ─────────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
ok()    { echo -e "${GREEN}[✓]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
err()   { echo -e "${RED}[✗]${NC} $*" >&2; }
info()  { echo -e "${CYAN}[·]${NC} $*"; }
banner(){ echo -e "\n${BOLD}$*${NC}\n"; }

# ── 설정 ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

FRONTEND_PORT="${FRONTEND_PORT:-8501}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
OLLAMA_URL="${OLLAMA_URL:-http://host.docker.internal:11434}"
MODE="${1:-docker}"

COMPOSE_FILE="$SCRIPT_DIR/docker-compose.yml"
PID_DIR="/tmp/mdf_pids"
LOG_DIR="/tmp/mdf_logs"

# ── 헬퍼 ─────────────────────────────────────────────────────────────────────

check_docker() {
    if ! command -v docker &>/dev/null; then
        err "Docker가 설치되어 있지 않습니다."; exit 1
    fi
    if ! docker info &>/dev/null; then
        err "Docker 데몬이 실행 중이 아닙니다. 'sudo systemctl start docker' 실행 후 재시도하세요."; exit 1
    fi
}

port_open() {
    bash -c ":> /dev/tcp/127.0.0.1/$1" 2>/dev/null
}

wait_healthy() {
    # docker compose up --wait 지원 여부 확인 (Compose v2.1+)
    if docker compose up --help 2>&1 | grep -q -- '--wait'; then
        echo "--wait 지원 확인됨"
        return 0
    fi
    return 1
}

show_urls() {
    local ext_ip
    ext_ip=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost")
    echo ""
    echo -e "${BOLD}═══════════════════════════════════════════════${NC}"
    echo -e "${BOLD}  MotorDriveForge 준비 완료${NC}"
    echo -e "${BOLD}═══════════════════════════════════════════════${NC}"
    ok "Streamlit UI  → http://localhost:$FRONTEND_PORT"
    ok "              → http://$ext_ip:$FRONTEND_PORT  (네트워크)"
    ok "Backend API   → http://localhost:$BACKEND_PORT/docs"
    ok "Qdrant        → http://localhost:6333/dashboard"
    echo ""
    info "로그: ./start.sh logs"
    info "종료: ./start.sh stop"
    echo ""
}

# ── STATUS ───────────────────────────────────────────────────────────────────

cmd_status() {
    banner "MotorDriveForge 서비스 상태"

    # Docker 컨테이너 상태
    if command -v docker &>/dev/null; then
        echo "[ Docker 컨테이너 ]"
        docker compose -f "$COMPOSE_FILE" ps 2>/dev/null || true
        echo ""
    fi

    # 포트 연결 확인
    echo "[ 포트 연결 ]"
    port_open 6333 && ok "Qdrant       :6333" || warn "Qdrant       :6333 오프라인"
    port_open "$BACKEND_PORT"  && ok "Backend      :$BACKEND_PORT" || warn "Backend      :$BACKEND_PORT 오프라인"
    port_open "$FRONTEND_PORT" && ok "Frontend     :$FRONTEND_PORT" || warn "Frontend     :$FRONTEND_PORT 오프라인"

    # Ollama 확인
    echo ""
    echo "[ Ollama ]"
    if port_open 11434; then
        local models
        models=$(curl -s http://localhost:11434/api/tags \
            | python3 -c "import sys,json; d=json.load(sys.stdin); print(', '.join(m['name'] for m in d.get('models',[])[:4]))" 2>/dev/null || echo "?")
        ok "Ollama       :11434 (Docker 컨테이너)"
        info "  로드된 모델: $models"
    else
        warn "Ollama       :11434 오프라인"
    fi
    echo ""
}

# ── STOP ─────────────────────────────────────────────────────────────────────

cmd_stop() {
    banner "서비스 중지"
    check_docker

    info "Docker 컨테이너 중지..."
    docker compose -f "$COMPOSE_FILE" down --remove-orphans
    ok "Docker 컨테이너 중지 완료"

    # dev 모드로 실행한 프로세스도 정리
    if [ -d "$PID_DIR" ]; then
        for f in "$PID_DIR"/*.pid; do
            [ -f "$f" ] || continue
            local pid; pid=$(cat "$f")
            kill "$pid" 2>/dev/null && info "PID $pid 종료"
            rm -f "$f"
        done
    fi
    ok "모든 서비스 중지 완료"
}

# ── DOCKER 모드 (기본) ────────────────────────────────────────────────────────

cmd_docker() {
    banner "MotorDriveForge — Docker 모드 시작"
    check_docker

    # .env 파일 생성 (docker-compose에서 읽음)
    cat > "$SCRIPT_DIR/.env" <<EOF
OLLAMA_URL=${OLLAMA_URL}
FRONTEND_PORT=${FRONTEND_PORT}
BACKEND_PORT=${BACKEND_PORT}
EOF

    info "이미지 빌드 및 컨테이너 시작..."
    echo ""

    # --wait: 모든 서비스의 healthcheck가 통과할 때까지 대기 (Compose v2.1+)
    if docker compose up --help 2>&1 | grep -q -- '--wait'; then
        docker compose -f "$COMPOSE_FILE" up -d --build --wait
        ok "모든 서비스 헬스체크 통과"
    else
        # --wait 미지원 시 수동 대기
        warn "--wait 미지원 버전. 수동으로 준비 대기합니다..."
        docker compose -f "$COMPOSE_FILE" up -d --build

        local timeout=120 elapsed=0
        info "서비스 준비 대기 중 (최대 ${timeout}초)..."
        while ! (port_open 6333 && port_open "$BACKEND_PORT" && port_open "$FRONTEND_PORT"); do
            sleep 3; elapsed=$((elapsed+3))
            if [ $elapsed -ge $timeout ]; then
                err "서비스가 ${timeout}초 내에 시작되지 않았습니다."
                docker compose -f "$COMPOSE_FILE" logs --tail=30
                exit 1
            fi
            echo -n "."
        done
        echo ""
        ok "모든 서비스 준비 완료"
    fi

    show_urls
}

# ── DEV 모드 ─────────────────────────────────────────────────────────────────

cmd_dev() {
    banner "MotorDriveForge — Dev 모드 (Qdrant=Docker, Backend/Frontend=Python)"
    check_docker
    mkdir -p "$PID_DIR" "$LOG_DIR"

    # Qdrant
    info "Qdrant 시작..."
    if port_open 6333; then
        ok "Qdrant 이미 실행 중"
    else
        docker compose -f "$COMPOSE_FILE" up -d qdrant > /dev/null 2>&1
        local i=0
        while ! port_open 6333; do
            sleep 2; i=$((i+2)); [ $i -lt 30 ] || { err "Qdrant 시작 실패"; exit 1; }
        done
        ok "Qdrant 준비 완료"
    fi

    # 패키지 확인
    info "Python 패키지 확인..."
    python3 -c "import fastapi, streamlit, uvicorn" 2>/dev/null || {
        warn "패키지 설치 중..."
        pip install -q -r "$SCRIPT_DIR/backend/requirements.txt"
        pip install -q -r "$SCRIPT_DIR/frontend/requirements.txt"
    }

    # Backend
    info "Backend 시작 (포트 $BACKEND_PORT)..."
    pkill -f "uvicorn backend.main" 2>/dev/null || true; sleep 1
    OLLAMA_URL="$OLLAMA_URL" \
    python3 -m uvicorn backend.main:app \
        --host 0.0.0.0 --port "$BACKEND_PORT" \
        > "$LOG_DIR/backend.log" 2>&1 &
    echo $! > "$PID_DIR/backend.pid"
    local i=0
    while ! port_open "$BACKEND_PORT"; do
        sleep 2; i=$((i+2)); [ $i -lt 30 ] || { err "Backend 시작 실패"; exit 1; }
    done
    ok "Backend 준비 완료"

    # Frontend
    info "Frontend 시작 (포트 $FRONTEND_PORT)..."
    pkill -f "streamlit run frontend" 2>/dev/null || true; sleep 1
    BACKEND_URL="http://localhost:$BACKEND_PORT" \
    python3 -m streamlit run frontend/app.py \
        --server.port "$FRONTEND_PORT" \
        --server.address 0.0.0.0 \
        > "$LOG_DIR/frontend.log" 2>&1 &
    echo $! > "$PID_DIR/frontend.pid"
    local i=0
    while ! port_open "$FRONTEND_PORT"; do
        sleep 2; i=$((i+2)); [ $i -lt 30 ] || { err "Frontend 시작 실패"; exit 1; }
    done
    ok "Frontend 준비 완료"

    show_urls
    info "Backend 로그  → tail -f $LOG_DIR/backend.log"
    info "Frontend 로그 → tail -f $LOG_DIR/frontend.log"
}

# ── MAIN ─────────────────────────────────────────────────────────────────────

case "$MODE" in
    docker|"")  cmd_docker ;;
    dev)        cmd_dev    ;;
    stop)       cmd_stop   ;;
    status)     cmd_status ;;
    restart)    cmd_stop; sleep 2; cmd_docker ;;
    logs)       check_docker; docker compose -f "$COMPOSE_FILE" logs -f ;;
    *)
        echo "사용법: $0 [docker|dev|stop|status|restart|logs]"
        exit 1
        ;;
esac
