#!/bin/bash -l
# Load the teacher in vLLM on one H200 and generate for a few synthetic PRs.
#SBATCH --job-name=prpilot-vllm-smoke
#SBATCH --account=eece7398.202710
#SBATCH --partition=gpu-short
#SBATCH --gres=gpu:h200:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=01:30:00
#SBATCH --output=/scratch/%u/prpilot/logs/%x-%j.out

set -euo pipefail
cd "${SLURM_SUBMIT_DIR}"
source training/slurm/env.sh
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate "${PRPILOT_VLLM_ENV}"
export HF_HUB_OFFLINE=1  # the model must already be downloaded; never fetch on a GPU node

nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv
python -c "import torch, vllm; print('torch', torch.__version__, 'cuda', torch.version.cuda, 'available', torch.cuda.is_available()); print('vllm', vllm.__version__)"
# is_available() only checks that a GPU exists; this actually starts CUDA, so a driver/CUDA
# mismatch fails here in seconds instead of after loading the model.
python -c "import torch; x = torch.ones(1, device='cuda'); print('cuda init ok:', x.item(), torch.cuda.get_device_name())"

python training/teacher/vllm_smoke.py --out "${PRPILOT_SCRATCH}/outputs/vllm_smoke_${SLURM_JOB_ID}.json"
cp "${PRPILOT_SCRATCH}/outputs/vllm_smoke_${SLURM_JOB_ID}.json" "${PRPILOT_KEEP}/"
