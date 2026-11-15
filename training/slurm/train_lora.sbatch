#!/bin/bash -l
# Phase 3: LoRA fine-tuning of the student on one H200 (training/sft/train.py).
# Submit from the code copy on scratch, with sft_train.jsonl / sft_val.jsonl in
# $PRPILOT_SCRATCH/data and the training env built (training/slurm/setup_train_env.sh):
#   sbatch --time=08:00:00 training/slurm/train_lora.sbatch <run name> <config> [extra args]
# Resubmitting the same run name continues from its last checkpoint. Checkpoints and the
# MLflow store are copied to ~/prpilot-keep when the job ends.
#SBATCH --job-name=prpilot-train
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
conda activate "${PRPILOT_TRAIN_ENV}"
export HF_HUB_OFFLINE=1
export MLFLOW_TRACKING_URI="file://${PRPILOT_SCRATCH}/mlruns"
export TOKENIZERS_PARALLELISM=false

NAME="$1"; CONFIG="$2"; shift 2
DATA="${PRPILOT_SCRATCH}/data"
OUT="${PRPILOT_SCRATCH}/checkpoints/${NAME}"
for f in sft_train.jsonl sft_val.jsonl; do
  test -f "${DATA}/${f}" || { echo "missing ${DATA}/${f}; scp it from the laptop first"; exit 1; }
done

echo "code commit: $(cat COMMIT 2>/dev/null || echo unknown)"
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv
# Training prompts must equal inference prompts token for token; stop before any GPU time.
python training/sft/format.py --check "${DATA}/sft_train.jsonl" --model "${BASE_REPO}" --revision "${BASE_REVISION}"

python training/sft/train.py --config "${CONFIG}" --train "${DATA}/sft_train.jsonl" \
  --val "${DATA}/sft_val.jsonl" --out "${OUT}" --resume-if-exists "$@"

mkdir -p "${PRPILOT_KEEP}/checkpoints"
rsync -a --exclude "optimizer.pt" "${OUT}" "${PRPILOT_KEEP}/checkpoints/"
rsync -a "${PRPILOT_SCRATCH}/mlruns" "${PRPILOT_KEEP}/"
echo "copied adapters and MLflow store to ${PRPILOT_KEEP}"
