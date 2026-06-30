#!/usr/bin/env bash
# Run AuditRouter's full billed-vs-paid reconciliation with the PRIVATE
# PrivilegePod brain: every LLM call goes to an open-source model (Qwen2.5) on
# YOUR Runpod GPU via Flash. Nothing reaches Anthropic/OpenAI; the routing log
# reads `qwen-2.5 (runpod)` at $0.00 to any vendor.
#
# Prereqs: `flash dev` is serving llm_worker, and the privilege_llm endpoint is
# healthy (one successful POST /llm_worker/runsync).
#
# Usage:  FLASH_PORT=8890 ./run_private_demo.sh
set -euo pipefail

FLASH_PORT="${FLASH_PORT:-8888}"
MODEL="${PRIVILEGE_MODEL:-qwen2.5-3b-instruct}"

export LLM_BACKEND=flash
export FLASH_ENDPOINT_URL="http://localhost:${FLASH_PORT}"
export FLASH_ENDPOINT_NAME=llm_worker
export FLASH_TIMEOUT_S=900
# every routing tier resolves to the private model, so the log shows runpod/$0
export AUDITROUTER_MODEL_BULK="$MODEL"
export AUDITROUTER_MODEL_STANDARD="$MODEL"
export AUDITROUTER_MODEL_HIGH="$MODEL"
export AUDITROUTER_MODEL_CRITICAL="$MODEL"
unset ANTHROPIC_API_KEY    # guarantee no hosted-vendor fallback

cd /Users/jiyunhyo/Documents/AuditRouter/backend
# shellcheck disable=SC1091
source .venv/bin/activate
echo "Running AuditRouter privately via $FLASH_ENDPOINT_URL/$FLASH_ENDPOINT_NAME ($MODEL)"
python -m auditrouter.demo
