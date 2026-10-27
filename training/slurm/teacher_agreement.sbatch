#!/bin/bash -l
# Task 0.6: teacher triage of the 200 sampled train-window PRs on one H200.
# Expects the inputs file at $PRPILOT_SCRATCH/data/teacher_agreement_inputs.jsonl (copied from
# the laptop with scp). Output is copied to ~/prpilot-keep when done.
#SBATCH --job-name=prpilot-teacher-agree
#SBATCH --account=eece7398.202710
#SBATCH --partition=gpu
#SBATCH --gres=gpu:h200:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=04:00:00
#SBATCH --output=/scratch/%u/prpilot/logs/%x-%j.out

set -euo pipefail
cd "${SLURM_SUBMIT_DIR}"
source training/slurm/env.sh
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate "${PRPILOT_VLLM_ENV}"
export HF_HUB_OFFLINE=1

INPUTS="${PRPILOT_SCRATCH}/data/teacher_agreement_inputs.jsonl"
OUT="${PRPILOT_SCRATCH}/outputs/teacher_agreement_${SLURM_JOB_ID}.jsonl"
test -f "${INPUTS}" || { echo "missing ${INPUTS}; scp it from the laptop first"; exit 1; }

nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv
python -c "import torch; x = torch.ones(1, device='cuda'); print('cuda init ok:', x.item(), torch.cuda.get_device_name())"
python training/teacher/teacher_agreement.py --inputs "${INPUTS}" --out "${OUT}"
cp "${OUT}" "${PRPILOT_KEEP}/"
echo "copied to ${PRPILOT_KEEP}/$(basename "${OUT}")"
