#!/bin/bash
# =============================================================================
# OpenClaw Admin Console — Startup script
# Reads secrets from SSM, sets environment, starts FastAPI server.
# Used by systemd service: ExecStart=/opt/admin-console/start.sh
# =============================================================================

# Read stack name from /etc/openclaw/env (set during deploy)
STACK_NAME="${STACK_NAME:-openclaw-multitenancy}"
SSM_REGION="${SSM_REGION:-${GATEWAY_REGION:-us-east-1}}"

# Source environment file if it exists
if [ -f /etc/openclaw/env ]; then
    set -o allexport
    . /etc/openclaw/env
    set +o allexport
fi

# Read secrets from SSM (override env file values if present in SSM)
export ADMIN_PASSWORD="${ADMIN_PASSWORD:-$(aws ssm get-parameter --name /openclaw/${STACK_NAME}/admin-password --with-decryption --query Parameter.Value --output text --region ${SSM_REGION} 2>/dev/null || echo '')}"
export JWT_SECRET="${JWT_SECRET:-$(aws ssm get-parameter --name /openclaw/${STACK_NAME}/jwt-secret --with-decryption --query Parameter.Value --output text --region ${SSM_REGION} 2>/dev/null || echo '')}"

# Defaults
export AWS_REGION="${AWS_REGION:-us-east-2}"
export GATEWAY_REGION="${GATEWAY_REGION:-${SSM_REGION}}"
export CONSOLE_PORT="${CONSOLE_PORT:-8099}"
export TENANT_ROUTER_URL="${TENANT_ROUTER_URL:-http://localhost:8090}"

cd /opt/admin-console/server
exec /opt/admin-venv/bin/python main.py
