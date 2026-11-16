#!/bin/bash -l
# Phase 2 system pass: serve a model with vLLM on one H200 and run the triage agent (with
# validator retries) over a prompt pack (training/baselines/agent_replies.py).
# Submit from the code copy on scratch, with the pack in $PRPILOT_SCRATCH/data and the agent
# env built (training/slurm/setup_agent_env.sh):
#   sbatch --time=02:00:00 training/slurm/agent_replies.sbatch <name> <pack file> \
#       <model repo> <revision> <max model len> [extra args]
# Fine-tuned student: LORA=<checkpoint dir> sbatch ... serves base + that LoRA adapter as the model.
# Replies and their .meta.json land in $PRPILOT_SCRATCH/outputs and are copied to ~/prpilot-keep.
#SBATCH --job-name=prpilot-agent
#SBATCH --account=eece7398.202710
#SBATCH --partition=gpu
#SBATCH --gres=gpu:h200:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --output=/scratch/%u/prpilot/logs/%x-%j.out

set -euo pipefail
cd "${SLURM_SUBMIT_DIR}"
source training/slurm/env.sh
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate "${PRPILOT_VLLM_ENV}"
export HF_HUB_OFFLINE=1
# Explorer sets http_proxy on every node; without this, requests to our local server (curl
# health checks and the agent's HTTP client) are sent to the cluster proxy and never arrive.
export no_proxy="127.0.0.1,localhost${no_proxy:+,${no_proxy}}" NO_PROXY="127.0.0.1,localhost${NO_PROXY:+,${NO_PROXY}}"

NAME="$1"; PACK="${PRPILOT_SCRATCH}/data/$2"; MODEL="$3"; REVISION="$4"; MAX_LEN="$5"; shift 5
test -f "${PACK}" || { echo "missing ${PACK}; scp it from the laptop first"; exit 1; }
test -x "${PRPILOT_AGENT_ENV}/bin/python" || { echo "missing agent env; run setup_agent_env.sh"; exit 1; }
OUT="${PRPILOT_SCRATCH}/outputs/${NAME}_${SLURM_JOB_ID}.jsonl"
PORT=$((20000 + SLURM_JOB_ID % 20000))
SERVED="prpilot-model"

echo "code commit: $(cat COMMIT 2>/dev/null || echo unknown)"
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv
SERVE_ARGS=(--served-model-name "${SERVED}"); AGENT_ARGS=()
if [ -n "${LORA:-}" ]; then
  test -f "${LORA}/adapter_model.safetensors" || { echo "no adapter in ${LORA}"; exit 1; }
  # The base is served under another name; the agent asks for SERVED, which is the adapter.
  SERVE_ARGS=(--served-model-name prpilot-base --enable-lora --max-lora-rank 16
              --max-loras 1 --lora-modules "${SERVED}=${LORA}")
  AGENT_ARGS=(--adapter "${LORA}")
fi
vllm serve "${MODEL}" --revision "${REVISION}" "${SERVE_ARGS[@]}" \
  --host 127.0.0.1 --port "${PORT}" --max-model-len "${MAX_LEN}" \
  --gpu-memory-utilization 0.92 --seed 0 &
SERVER=$!
trap 'kill ${SERVER} 2>/dev/null || true' EXIT

# First start on new settings compiles kernels (~6 min); allow 20.
for _ in $(seq 1 240); do
  curl -sf "http://127.0.0.1:${PORT}/health" >/dev/null && break
  kill -0 "${SERVER}" 2>/dev/null || { echo "vllm serve exited"; exit 1; }
  sleep 5
done
curl -sf "http://127.0.0.1:${PORT}/health" >/dev/null || { echo "server not ready after 20 min"; exit 1; }
echo "server ready on port ${PORT}"

"${PRPILOT_AGENT_ENV}/bin/python" training/baselines/agent_replies.py --name "${NAME}" \
  --pack "${PACK}" --out "${OUT}" --base-url "http://127.0.0.1:${PORT}/v1" \
  --served-model "${SERVED}" --model "${MODEL}" --revision "${REVISION}" \
  --max-model-len "${MAX_LEN}" "${AGENT_ARGS[@]}" "$@"
cp "${OUT}" "${OUT%.jsonl}.meta.json" "${PRPILOT_KEEP}/"
echo "copied to ${PRPILOT_KEEP}"
