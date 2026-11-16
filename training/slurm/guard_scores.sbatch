#!/bin/bash -l
# PromptGuard 2 field scores for one or more PR files on one H200
# (training/screening/guard_scores.py). Uses the training env (torch, transformers) and a
# copy of the pinned PromptGuard snapshot at $PRPILOT_SCRATCH/models/prompt-guard-2-86M
# (copied from the laptop's Hugging Face cache: the gated model has no token on Explorer).
#   sbatch training/slurm/guard_scores.sbatch <name> <input file in $PRPILOT_SCRATCH/data>... [-- extra]
# Each input writes $PRPILOT_SCRATCH/outputs/guard_<name>_<input stem>_<job>.jsonl (+ meta),
# copied to ~/prpilot-keep.
#SBATCH --job-name=prpilot-guard
#SBATCH --account=eece7398.202710
#SBATCH --partition=gpu
#SBATCH --gres=gpu:h200:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=02:00:00
#SBATCH --output=/scratch/%u/prpilot/logs/%x-%j.out

set -euo pipefail
cd "${SLURM_SUBMIT_DIR}"
source training/slurm/env.sh
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate "${PRPILOT_TRAIN_ENV}"
export HF_HUB_OFFLINE=1 PYTHONPATH="${PWD}"

MODEL_DIR="${PRPILOT_SCRATCH}/models/prompt-guard-2-86M"
test -f "${MODEL_DIR}/model.safetensors" || { echo "copy the PromptGuard snapshot to ${MODEL_DIR}"; exit 1; }
NAME="$1"; shift
INPUTS=(); while [ $# -gt 0 ] && [ "$1" != "--" ]; do INPUTS+=("$1"); shift; done
[ "${1:-}" = "--" ] && shift

echo "code commit: $(cat COMMIT 2>/dev/null || echo unknown)"
nvidia-smi --query-gpu=name,memory.total --format=csv
for IN in "${INPUTS[@]}"; do
  test -f "${PRPILOT_SCRATCH}/data/${IN}" || { echo "missing ${IN}"; exit 1; }
  OUT="${PRPILOT_SCRATCH}/outputs/guard_${NAME}_${IN%.jsonl}_${SLURM_JOB_ID}.jsonl"
  python training/screening/guard_scores.py --input "${PRPILOT_SCRATCH}/data/${IN}" \
    --out "${OUT}" --local-dir "${MODEL_DIR}" "$@"
  cp "${OUT}" "${OUT%.jsonl}.meta.json" "${PRPILOT_KEEP}/"
done
echo "copied to ${PRPILOT_KEEP}"
