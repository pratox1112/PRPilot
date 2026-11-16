#!/bin/bash -l
# GGUF re-test of the student (roadmap §6.3): serve the quantized file with llama.cpp's
# llama-server (CUDA, one H200) and run either the raw replies (openai_replies.py) or the
# triage agent with validator retries (agent_replies.py) over a prompt pack.
# Needs llama.cpp (setup_llamacpp.sh), the export (export_gguf.sbatch) and the agent env:
#   sbatch training/slurm/gguf_replies.sbatch <name> <pack file> <gguf file> <raw|system> [extra]
# Replies and their .meta.json land in $PRPILOT_SCRATCH/outputs and are copied to ~/prpilot-keep.
#SBATCH --job-name=prpilot-gguf
#SBATCH --account=eece7398.202710
#SBATCH --partition=gpu
#SBATCH --gres=gpu:h200:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=02:00:00
#SBATCH --output=/scratch/%u/prpilot/logs/%x-%j.out

set -euo pipefail
cd "${SLURM_SUBMIT_DIR}"
source training/slurm/env.sh
module load cuda/12.8.0  # runtime libraries for the CUDA build
# Explorer sets http_proxy on every node; local requests must not go to the cluster proxy.
export no_proxy="127.0.0.1,localhost${no_proxy:+,${no_proxy}}" NO_PROXY="127.0.0.1,localhost${NO_PROXY:+,${NO_PROXY}}"

NAME="$1"; PACK="${PRPILOT_SCRATCH}/data/$2"; GGUF="$3"; MODE="$4"; shift 4
test -f "${PACK}" || { echo "missing ${PACK}"; exit 1; }
test -f "${GGUF}" || { echo "missing ${GGUF}; run export_gguf.sbatch"; exit 1; }
test -x "${PRPILOT_AGENT_ENV}/bin/python" || { echo "missing agent env"; exit 1; }
OUT="${PRPILOT_SCRATCH}/outputs/${NAME}_${SLURM_JOB_ID}.jsonl"
PORT=$((20000 + SLURM_JOB_ID % 20000))
SERVED="prpilot-model"
SLOTS=16

echo "code commit: $(cat COMMIT 2>/dev/null || echo unknown)"
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv
# -c is the KV cache shared by all slots: 16 slots x 16,384 tokens, the vLLM max model length.
"${LLAMACPP_DIR}/build/bin/llama-server" -m "${GGUF}" -a "${SERVED}" --jinja \
  --host 127.0.0.1 --port "${PORT}" -ngl 99 -np "${SLOTS}" -c $((SLOTS * 16384)) &
SERVER=$!
trap 'kill ${SERVER} 2>/dev/null || true' EXIT
for _ in $(seq 1 120); do
  curl -sf "http://127.0.0.1:${PORT}/health" >/dev/null && break
  kill -0 "${SERVER}" 2>/dev/null || { echo "llama-server exited"; exit 1; }
  sleep 5
done
curl -sf "http://127.0.0.1:${PORT}/health" >/dev/null || { echo "server not ready after 10 min"; exit 1; }
echo "server ready on port ${PORT}"

URL="http://127.0.0.1:${PORT}/v1"
if [ "${MODE}" = raw ]; then
  "${PRPILOT_AGENT_ENV}/bin/python" training/baselines/openai_replies.py --name "${NAME}" \
    --pack "${PACK}" --out "${OUT}" --base-url "${URL}" --served-model "${SERVED}" \
    --gguf "${GGUF}" --max-tokens 1024 --concurrency "${SLOTS}" "$@"
elif [ "${MODE}" = system ]; then
  "${PRPILOT_AGENT_ENV}/bin/python" training/baselines/agent_replies.py --name "${NAME}" \
    --pack "${PACK}" --out "${OUT}" --base-url "${URL}" --served-model "${SERVED}" \
    --model "$(basename "${GGUF}")" --revision "$(sha256sum "${GGUF}" | cut -d' ' -f1)" \
    --max-model-len 16384 --concurrency "${SLOTS}" "$@"
else
  echo "mode must be raw or system"; exit 1
fi
cp "${OUT}" "${OUT%.jsonl}.meta.json" "${PRPILOT_KEEP}/"
echo "copied to ${PRPILOT_KEEP}"
