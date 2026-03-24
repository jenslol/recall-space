#!/usr/bin/env bash
# upgrade-to-phase2.sh — Run this on the GMKtec to upgrade from Phase 1
# Usage: cd ~/recall-space && bash upgrade-to-phase2.sh
set -euo pipefail

echo "=== Recall Space — Upgrade to Phase 2 ==="
echo ""

# Safety check
if [ ! -f "docker-compose.yml" ]; then
    echo "ERROR: Run this from your recall-space directory"
    exit 1
fi

# 1. Stop current containers
echo "[→] Stopping current containers..."
docker compose down
echo "[✓] Containers stopped"
echo ""

# 2. Preserve data
echo "[→] Your data is safe — data/ and uploads/ are volume-mounted."
echo "    Database: data/recall.db"
echo "    Files:    uploads/"
echo ""

# 3. Rebuild with new processor service
echo "[→] Rebuilding containers (this adds the job processor)..."
docker compose build --no-cache
echo "[✓] Build complete"
echo ""

# 4. Check .env
if [ ! -f ".env" ]; then
    echo "[→] Creating .env from template..."
    cp .env.example .env
    echo "[!] IMPORTANT: Edit .env and set AI_WORKER_URL to your gaming PC's IP"
    echo "    nano .env"
    echo ""
else
    echo "[✓] .env exists"
    if grep -q "192.168.1.100" .env; then
        echo "[!] WARNING: AI_WORKER_URL still points to 192.168.1.100 (the default)"
        echo "    Update it to your gaming PC's actual IP:"
        echo "    nano .env"
        echo ""
    fi
fi

# 5. Start everything
echo "[→] Starting all services..."
docker compose up -d
echo ""

# 6. Show status
echo "[✓] Phase 2 deployed!"
echo ""
echo "Services running:"
docker compose ps --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}"
echo ""
echo "Watch the processor logs:"
echo "  docker logs -f recall-processor"
echo ""
echo "The processor will poll every 10s for pending jobs."
echo "If the AI worker (gaming PC) is offline, jobs queue up safely."
