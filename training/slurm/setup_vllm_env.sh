#!/bin/bash -l
# Create (or re-create after a scratch purge) the teacher inference env on scratch.
# Run as a batch job (an interactive srun dies with your SSH connection):
#   cd /scratch/$USER/prpilot/code
#   sbatch --account=eece7398.202710 -p short -c 8 --mem=16G -t 06:00:00 -o /scratch/$USER/prpilot/logs/setup-env-%j.out --wrap "bash training/slurm/setup_vllm_env.sh"
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
  # Must pin the CUDA 12.9 builds (the H200 driver cannot run CUDA 13) and must not contain
  # packages that only come from ~/.local leaks.
  grep -qx "vllm==0.31.0+cu129" "$1" &&
    grep -qx "torch==2.13.0+cu129" "$1" &&
    ! grep -qiE "^(xformers|diffusers|timm)==" "$1"
}

echo "$(date +%T) creating conda env"
conda create -y -p "${PRPILOT_VLLM_ENV}" python=3.12
conda activate "${PRPILOT_VLLM_ENV}"

# uv installs packages in parallel, which is much faster than pip on network storage.
# Same version as on the laptop. Its cache lives on scratch, never in the full home dir.
pip install uv==0.12.22
export UV_CACHE_DIR="${PRPILOT_SCRATCH}/uv-cache"
export UV_LINK_MODE=copy
# The CUDA 12.9 builds live on PyTorch's and vLLM's own indexes; best-match lets uv take each
# package from whichever index has the pinned version (the lock file has no index lines).
install() {
  uv pip install --python "$(command -v python)" --index-strategy unsafe-best-match \
    --extra-index-url https://download.pytorch.org/whl/cu129 \
    --extra-index-url https://wheels.vllm.ai/0.31.0/cu129 "$@"
}

if [ -f "${LOCK}" ] && lock_is_sane "${LOCK}"; then
  echo "$(date +%T) installing exact versions from ${LOCK}"
  install -r "${LOCK}"
else
  if [ -f "${LOCK}" ]; then
    mv "${LOCK}" "${LOCK}.rejected"
    echo "lock file failed the sanity check; moved to ${LOCK}.rejected"
  fi
  echo "$(date +%T) installing from training/teacher/requirements-vllm.txt"
  install -r training/teacher/requirements-vllm.txt
  pip freeze --local > "${LOCK}"
  echo "recorded ${LOCK}"
fi
echo "$(date +%T) install finished"

python -c "import vllm, torch; print('vllm', vllm.__version__, 'torch', torch.__version__, 'cuda build', torch.version.cuda)"
# Fail loudly (and remove the env via the trap) if a CUDA 13 build slipped in.
python -c "import sys, torch; sys.exit(0 if torch.version.cuda.startswith('12.') else 'torch is not a CUDA 12 build')"
du -sh "${PRPILOT_VLLM_ENV}"
