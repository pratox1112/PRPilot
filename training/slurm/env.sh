# Shared environment for PRPilot jobs on Explorer. Source it: `source training/slurm/env.sh`.
# Model weights go to scratch (large, purged monthly, re-downloadable). The Hugging Face
# token stays in the default HF_HOME under $HOME, so the scratch purge cannot delete it.

export PRPILOT_SCRATCH="/scratch/${USER}/prpilot"
export PRPILOT_KEEP="${HOME}/prpilot-keep"
export HF_HUB_CACHE="${PRPILOT_SCRATCH}/hf-cache"
# The conda env lives on scratch too (home is too small). If the purge removes it, rebuild
# it with training/slurm/setup_vllm_env.sh.
export PRPILOT_VLLM_ENV="${PRPILOT_SCRATCH}/envs/prpilot-vllm"
export CONDA_PKGS_DIRS="${PRPILOT_SCRATCH}/conda-pkgs"
export PIP_NO_CACHE_DIR=1
export HF_HUB_DISABLE_TELEMETRY=1
export VLLM_NO_USAGE_STATS=1
export DO_NOT_TRACK=1

export TEACHER_REPO="Qwen/Qwen3.5-122B-A10B-GPTQ-Int4"
export TEACHER_REVISION="30cd92cba9707a9aba09d1e490ed4b66b78e9606"

mkdir -p "${HF_HUB_CACHE}" "${PRPILOT_SCRATCH}/envs" "${CONDA_PKGS_DIRS}" "${PRPILOT_SCRATCH}/outputs" "${PRPILOT_SCRATCH}/logs" "${PRPILOT_KEEP}"
