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
  # The lock is a complete list; --no-deps stops uv re-adding packages we removed on purpose.
  install --no-deps -r "${LOCK}"
else
  if [ -f "${LOCK}" ]; then
    mv "${LOCK}" "${LOCK}.rejected"
    echo "lock file failed the sanity check; moved to ${LOCK}.rejected"
  fi
  echo "$(date +%T) installing from training/teacher/requirements-vllm.txt"
  install -r training/teacher/requirements-vllm.txt
  # torchcodec (audio/video decoding) only exists as a CUDA 13 build for torch 2.13 and fails to
  # load (libnvrtc.so.13). vLLM treats it as optional and we run text-only, so remove it.
  uv pip uninstall --python "$(command -v python)" torchcodec
  pip freeze --local > "${LOCK}"
  echo "recorded ${LOCK}"
fi
echo "$(date +%T) install finished"
# Also covers lock files recorded before torchcodec was removed.
uv pip uninstall --python "$(command -v python)" torchcodec 2>/dev/null || true

# CUDA forward compatibility for the H200 nodes' driver 570 (see training/slurm/env.sh).
echo "$(date +%T) installing cuda-compat"
# `cuda-compat` is a metapackage; the libraries (user-space driver 580.95) are in cuda-compat-impl.
conda install -y -p "${PRPILOT_VLLM_ENV}" -c conda-forge --no-deps \
  cuda-compat=13.0.2 cuda-compat-impl=580.95.05
compat_lib=$(find "${PRPILOT_VLLM_ENV}" -name "libcuda.so.1" -not -path "*site-packages*" | head -1)
test -n "${compat_lib}" || { echo "cuda-compat installed but libcuda.so.1 not found"; false; }
echo "cuda-compat libcuda: ${compat_lib}"

python -c "import vllm, torch; print('vllm', vllm.__version__, 'torch', torch.__version__, 'cuda build', torch.version.cuda)"
# Fail loudly (and remove the env via the trap) if a CUDA 13 build slipped in.
python -c "import sys, torch; sys.exit(0 if torch.version.cuda.startswith('12.') else 'torch is not a CUDA 12 build')"
du -sh "${PRPILOT_VLLM_ENV}"
