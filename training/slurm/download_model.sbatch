#!/bin/bash -l
# Download any model at a pinned revision into the scratch HF cache. CPU only.
#   sbatch training/slurm/download_model.sbatch <hf repo> <revision>
#SBATCH --job-name=prpilot-dl-model
#SBATCH --account=eece7398.202710
#SBATCH --partition=short
#SBATCH --cpus-per-task=4
#SBATCH --mem=8G
#SBATCH --time=02:00:00
#SBATCH --output=/scratch/%u/prpilot/logs/%x-%j.out

set -euo pipefail
cd "${SLURM_SUBMIT_DIR}"
source training/slurm/env.sh
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate "${PRPILOT_VLLM_ENV}"

REPO="$1"; REVISION="$2"
echo "downloading ${REPO}@${REVISION} to ${HF_HUB_CACHE}"
hf download "${REPO}" --revision "${REVISION}"
du -shL --apparent-size "${HF_HUB_CACHE}/models--${REPO//\//--}/snapshots/${REVISION}"
echo "done"
