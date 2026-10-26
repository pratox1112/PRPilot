#!/bin/bash -l
# Create (or re-create after a scratch purge) the teacher inference env on scratch.
# Run on a compute node, not the login node (conda and pip get killed there):
#   srun --account=eece7398.202710 -p short --cpus-per-task=4 --mem=16G --time=02:00:00 --pty bash
#   cd /scratch/$USER/prpilot/code && bash training/slurm/setup_vllm_env.sh
# If ~/prpilot-keep/requirements-vllm.lock.txt exists, the exact recorded versions are used.

set -euo pipefail
cd "$(dirname "$0")/../.."
source training/slurm/env.sh
source "$(conda info --base)/etc/profile.d/conda.sh"

LOCK="${PRPILOT_KEEP}/requirements-vllm.lock.txt"

if [ -d "${PRPILOT_VLLM_ENV}" ]; then
  echo "env already exists at ${PRPILOT_VLLM_ENV}; remove it first to rebuild"
  exit 1
fi

conda create -y -p "${PRPILOT_VLLM_ENV}" python=3.12
conda activate "${PRPILOT_VLLM_ENV}"

if [ -f "${LOCK}" ]; then
  echo "installing exact versions from ${LOCK}"
  pip install -r "${LOCK}"
else
  pip install -r training/teacher/requirements-vllm.txt
  pip freeze > "${LOCK}"
  echo "recorded ${LOCK}"
fi

python -c "import vllm, torch; print('vllm', vllm.__version__, 'torch', torch.__version__, 'cuda build', torch.version.cuda)"
du -sh "${PRPILOT_VLLM_ENV}"
