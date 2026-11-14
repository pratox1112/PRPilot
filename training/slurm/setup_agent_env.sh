#!/bin/bash -l
# Create (or re-create after a scratch purge) the agent env on scratch: the app's own locked
# dependencies (uv.lock, no dev/data groups) for running prpilot_core against a vLLM server.
# Run as a batch job:
#   cd /scratch/$USER/prpilot/code
#   sbatch --account=eece7398.202710 -p short -c 4 --mem=8G -t 01:00:00 -o /scratch/$USER/prpilot/logs/setup-agent-env-%j.out --wrap "bash training/slurm/setup_agent_env.sh"

set -euo pipefail
cd "$(dirname "$0")/../.."
source training/slurm/env.sh

export UV_PROJECT_ENVIRONMENT="${PRPILOT_AGENT_ENV}"
export UV_CACHE_DIR="${PRPILOT_SCRATCH}/cache/uv"
export UV_PYTHON_INSTALL_DIR="${PRPILOT_SCRATCH}/envs/uv-python"
export UV_PYTHON_PREFERENCE=only-managed

uv --version
uv sync --locked --no-dev --python 3.12
"${PRPILOT_AGENT_ENV}/bin/python" -c "import pydantic_ai, prpilot_core.agents.triage; print('agent env ok', pydantic_ai.__version__)"
