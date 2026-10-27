#!/bin/bash -l
# Create (or re-create after a scratch purge) the teacher inference env on scratch.
# Run as a batch job (an interactive srun dies with your SSH connection):
#   cd /scratch/$USER/prpilot/code
#   sbatch --account=eece7398.202710 -p short -c 4 --mem=16G -t 02:00:00 -o /scratch/$USER/prpilot/logs/setup-env-%j.out --wrap "bash training/slurm/setup_vllm_env.sh"
# If ~/prpilot-keep/requirements-vllm.lock.txt exists and passes the sanity check, the exact
# recorded versions are installed; otherwise the env is built from requirements-vllm.txt and
# the lock file is (re)written.

set -euo pipefail
cd "$(dirname "$0")/../.."
source training/slurm/env.sh
source "$(conda info --base)/etc/profile.d/conda.sh"

LOCK="${PRPILOT_KEEP}/requirements-vllm.lock.txt"

if [ -d "${PRPILOT_VLLM_ENV}" ]; then
  echo "env already exists at ${PRPILOT_VLLM_ENV}; remove it first to rebuild"
  exit 1
fi

# A failed install leaves a broken env behind; remove it so the next run can start clean.
trap 'echo "setup failed; removing ${PRPILOT_VLLM_ENV}"; rm -rf "${PRPILOT_VLLM_ENV}"' ERR

lock_is_sane() {
  # Must pin our vLLM and must not contain packages that only come from ~/.local leaks.
  grep -qx "vllm==0.31.0" "$1" && ! grep -qiE "^(xformers|diffusers|timm)==" "$1"
}

conda create -y -p "${PRPILOT_VLLM_ENV}" python=3.12
conda activate "${PRPILOT_VLLM_ENV}"

if [ -f "${LOCK}" ] && lock_is_sane "${LOCK}"; then
  echo "installing exact versions from ${LOCK}"
  pip install -r "${LOCK}"
else
  if [ -f "${LOCK}" ]; then
    mv "${LOCK}" "${LOCK}.rejected"
    echo "lock file failed the sanity check; moved to ${LOCK}.rejected"
  fi
  pip install -r training/teacher/requirements-vllm.txt
  pip freeze --local > "${LOCK}"
  echo "recorded ${LOCK}"
fi

python -c "import vllm, torch; print('vllm', vllm.__version__, 'torch', torch.__version__, 'cuda build', torch.version.cuda)"
du -sh "${PRPILOT_VLLM_ENV}"
