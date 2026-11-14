#!/bin/bash -l
# Phase 2: one model's replies to a prompt pack on one H200 (training/baselines/vllm_replies.py).
# Submit from the code copy on scratch, with the pack already copied to $PRPILOT_SCRATCH/data:
#   sbatch --time=02:00:00 training/slurm/baseline_replies.sbatch <name> <pack file> \
#       <model repo> <revision> <thinking on|off> <max tokens> <max model len> [extra args]
# Replies and their .meta.json land in $PRPILOT_SCRATCH/outputs and are copied to
# ~/prpilot-keep (they cost GPU time and scratch is purged monthly).
#SBATCH --job-name=prpilot-baseline
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

NAME="$1"; PACK="${PRPILOT_SCRATCH}/data/$2"; MODEL="$3"; REVISION="$4"
THINKING="$5"; MAX_TOKENS="$6"; MAX_LEN="$7"; shift 7
test -f "${PACK}" || { echo "missing ${PACK}; scp it from the laptop first"; exit 1; }
OUT="${PRPILOT_SCRATCH}/outputs/${NAME}_${SLURM_JOB_ID}.jsonl"

echo "code commit: $(cat COMMIT 2>/dev/null || echo unknown)"
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv
python training/baselines/vllm_replies.py --name "${NAME}" --pack "${PACK}" --out "${OUT}" \
  --model "${MODEL}" --revision "${REVISION}" --thinking "${THINKING}" \
  --max-tokens "${MAX_TOKENS}" --max-model-len "${MAX_LEN}" "$@"
cp "${OUT}" "${OUT%.jsonl}.meta.json" "${PRPILOT_KEEP}/"
echo "copied to ${PRPILOT_KEEP}"
