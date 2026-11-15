#!/bin/bash -l
# Create (or re-create after a scratch purge) the training env on scratch.
# Run as a batch job:
#   cd /scratch/$USER/prpilot/code
#   sbatch --account=eece7398.202710 -p short -c 8 --mem=16G -t 02:00:00 -J prpilot-setup-train -o /scratch/$USER/prpilot/logs/%x-%j.out --wrap "bash training/slurm/setup_train_env.sh"
# If ~/prpilot-keep/requirements-train.lock.txt exists, the exact recorded versions are
# installed; otherwise the env is built from training/sft/requirements-train.txt and the lock
# file is written.

set -euo pipefail
cd "$(dirname "$0")/../.."
source training/slurm/env.sh
source "$(conda info --base)/etc/profile.d/conda.sh"

LOCK="${PRPILOT_KEEP}/requirements-train.lock.txt"

if [ -d "${PRPILOT_TRAIN_ENV}" ]; then
  echo "env already exists at ${PRPILOT_TRAIN_ENV}; remove it first to rebuild"
  exit 1
fi
trap 'echo "setup failed; removing ${PRPILOT_TRAIN_ENV}"; rm -rf "${PRPILOT_TRAIN_ENV}"' ERR

echo "$(date +%T) creating conda env"
conda create -y -p "${PRPILOT_TRAIN_ENV}" python=3.12
conda activate "${PRPILOT_TRAIN_ENV}"
pip install uv==0.12.22
export UV_CACHE_DIR="${PRPILOT_SCRATCH}/uv-cache"
export UV_LINK_MODE=copy
install() {
  uv pip install --python "$(command -v python)" --index-strategy unsafe-best-match \
    --extra-index-url https://download.pytorch.org/whl/cu126 "$@"
}

if [ -f "${LOCK}" ] && grep -qx "torch==2.13.0+cu126" "${LOCK}"; then
  echo "$(date +%T) installing exact versions from ${LOCK}"
  install --no-deps -r "${LOCK}"
else
  echo "$(date +%T) installing from training/sft/requirements-train.txt"
  install -r training/sft/requirements-train.txt
  pip freeze --local > "${LOCK}"
  echo "recorded ${LOCK}"
fi
echo "$(date +%T) install finished"

python -c "import torch, transformers, peft; print('torch', torch.__version__, 'cuda build', torch.version.cuda, '| transformers', transformers.__version__, '| peft', peft.__version__)"
python -c "import sys, torch; sys.exit(0 if torch.version.cuda.startswith('12.') else 'torch is not a CUDA 12 build')"
du -sh "${PRPILOT_TRAIN_ENV}"
