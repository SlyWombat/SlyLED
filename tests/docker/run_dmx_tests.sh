#!/bin/bash
# Run SlyLED DMX fixture integration tests in Docker with isolated network.
#
# Usage: bash tests/docker/run_dmx_tests.sh
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
IMAGE_NAME="slyled-dmx-test"
NET_NAME="slyled-dmx-net"
SERVER_NAME="slyled-dmx-server"
EMU_NAME="slyled-dmx-emu"
TEST_NAME="slyled-dmx-tester"

echo "=== Building Docker image ==="
docker build -t "$IMAGE_NAME" -f "$SCRIPT_DIR/Dockerfile" "$PROJECT_DIR"

echo "=== Creating isolated network ==="
docker network create "$NET_NAME" 2>/dev/null || true

cleanup() {
    echo "=== Cleanup ==="
    docker rm -f "$SERVER_NAME" "$EMU_NAME" "$TEST_NAME" 2>/dev/null || true
    docker network rm "$NET_NAME" 2>/dev/null || true
}
trap cleanup EXIT

echo "=== Starting parent server ==="
docker run -d --name "$SERVER_NAME" --network "$NET_NAME" \
    "$IMAGE_NAME" \
    python desktop/shared/parent_server.py --port 8080 --no-browser

# Wait for server to be ready
echo "=== Waiting for server ==="
for i in $(seq 1 30); do
    if docker exec "$SERVER_NAME" python -c "
import urllib.request
try:
    urllib.request.urlopen('http://localhost:8080/status', timeout=2)
    exit(0)
except:
    exit(1)
" 2>/dev/null; then
        echo "Server ready after ${i}s"
        break
    fi
    sleep 1
done

echo "=== Starting emulated children (with DMX bridge) ==="
docker run -d --name "$EMU_NAME" --network "$NET_NAME" \
    "$IMAGE_NAME" \
    python tests/emulated_children.py --base-port 9000 --count 11

sleep 2

echo "=== Running DMX fixture tests ==="
docker run --rm --name "$TEST_NAME" --network "$NET_NAME" \
    "$IMAGE_NAME" \
    python tests/test_dmx_fixtures.py "${SERVER_NAME}:8080"

echo "=== Done ==="
