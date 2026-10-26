#!/bin/bash -l
# Download the teacher model (~79 GB) at its pinned revision into scratch. CPU only.
#SBATCH --job-name=prpilot-dl-teacher
#SBATCH --account=eece7398.202710
#SBATCH --partition=short
#SBATCH --cpus-per-task=4
#SBATCH --mem=8G
#SBATCH --time=06:00:00
#SBATCH --output=/scratch/%u/prpilot/logs/%x-%j.out

set -euo pipefail
cd "${SLURM_SUBMIT_DIR}"
source training/slurm/env.sh
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate "${PRPILOT_VLLM_ENV}"

echo "downloading ${TEACHER_REPO}@${TEACHER_REVISION} to ${HF_HUB_CACHE}"
hf download "${TEACHER_REPO}" --revision "${TEACHER_REVISION}"
du -sh "${HF_HUB_CACHE}/models--${TEACHER_REPO//\//--}"
echo "done"
