#!/bin/bash -l
# Phase 2 system pass: serve a model with vLLM on one H200 and run the triage agent (with
# validator retries) over a prompt pack (training/baselines/agent_replies.py).
# Submit from the code copy on scratch, with the pack in $PRPILOT_SCRATCH/data and the agent
# env built (training/slurm/setup_agent_env.sh):
#   sbatch --time=02:00:00 training/slurm/agent_replies.sbatch <name> <pack file> \
#       <model repo> <revision> <max model len> [extra args]
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

NAME="$1"; PACK="${PRPILOT_SCRATCH}/data/$2"; MODEL="$3"; REVISION="$4"; MAX_LEN="$5"; shift 5
test -f "${PACK}" || { echo "missing ${PACK}; scp it from the laptop first"; exit 1; }
test -x "${PRPILOT_AGENT_ENV}/bin/python" || { echo "missing agent env; run setup_agent_env.sh"; exit 1; }
OUT="${PRPILOT_SCRATCH}/outputs/${NAME}_${SLURM_JOB_ID}.jsonl"
PORT=$((20000 + SLURM_JOB_ID % 20000))
SERVED="prpilot-model"

echo "code commit: $(cat COMMIT 2>/dev/null || echo unknown)"
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv
vllm serve "${MODEL}" --revision "${REVISION}" --served-model-name "${SERVED}" \
  --host 127.0.0.1 --port "${PORT}" --max-model-len "${MAX_LEN}" \
  --gpu-memory-utilization 0.92 --seed 0 &
SERVER=$!
trap 'kill ${SERVER} 2>/dev/null || true' EXIT

for _ in $(seq 1 120); do
  curl -sf "http://127.0.0.1:${PORT}/health" >/dev/null && break
  kill -0 "${SERVER}" 2>/dev/null || { echo "vllm serve exited"; exit 1; }
  sleep 5
done
curl -sf "http://127.0.0.1:${PORT}/health" >/dev/null || { echo "server not ready after 10 min"; exit 1; }
echo "server ready on port ${PORT}"

"${PRPILOT_AGENT_ENV}/bin/python" training/baselines/agent_replies.py --name "${NAME}" \
  --pack "${PACK}" --out "${OUT}" --base-url "http://127.0.0.1:${PORT}/v1" \
  --served-model "${SERVED}" --model "${MODEL}" --revision "${REVISION}" \
  --max-model-len "${MAX_LEN}" "$@"
cp "${OUT}" "${OUT%.jsonl}.meta.json" "${PRPILOT_KEEP}/"
echo "copied to ${PRPILOT_KEEP}"
