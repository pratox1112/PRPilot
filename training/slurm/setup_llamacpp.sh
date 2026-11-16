#!/bin/bash -l
# Build llama.cpp (pinned release) on scratch, for the GGUF export and re-test of the student.
# CUDA build for the H200s (sm_90); compiling needs no GPU, so run it as a CPU batch job:
#   cd /scratch/$USER/prpilot/code
#   sbatch --account=eece7398.202710 -p short -c 16 --mem=32G -t 01:30:00 -J prpilot-setup-llamacpp -o /scratch/$USER/prpilot/logs/%x-%j.out --wrap "bash training/slurm/setup_llamacpp.sh"
# The release tarball's SHA-256 is recorded in ~/prpilot-keep/llamacpp.sha256 on the first build
# and checked on every rebuild (scratch is purged monthly).

set -euo pipefail
cd "$(dirname "$0")/../.."
source training/slurm/env.sh

SRC="${LLAMACPP_DIR}"
TARBALL="${PRPILOT_SCRATCH}/llama.cpp-${LLAMACPP_VERSION}.tar.gz"
PIN="${PRPILOT_KEEP}/llamacpp.sha256"

if [ -x "${SRC}/build/bin/llama-server" ]; then
  echo "llama.cpp already built at ${SRC}; remove it first to rebuild"
  exit 1
fi
trap 'echo "build failed; removing ${SRC}"; rm -rf "${SRC}"' ERR

echo "$(date +%T) downloading llama.cpp ${LLAMACPP_VERSION}"
curl -fsSL -o "${TARBALL}" \
  "https://github.com/ggml-org/llama.cpp/archive/refs/tags/${LLAMACPP_VERSION}.tar.gz"
SHA="$(sha256sum "${TARBALL}" | cut -d' ' -f1)"
if [ -f "${PIN}" ]; then
  [ "$(cat "${PIN}")" = "${SHA}" ] || { echo "tarball hash ${SHA} != recorded $(cat "${PIN}")"; exit 1; }
else
  echo "${SHA}" > "${PIN}"
  echo "recorded ${PIN}"
fi
echo "tarball sha256 ${SHA}"
mkdir -p "${SRC}"
tar -xzf "${TARBALL}" -C "${SRC}" --strip-components=1

module load cmake/3.30.2 cuda/12.8.0
echo "$(date +%T) building (CUDA $(nvcc --version | grep -o 'release [0-9.]*'))"
cmake -S "${SRC}" -B "${SRC}/build" -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=90 \
  -DLLAMA_CURL=OFF -DCMAKE_BUILD_TYPE=Release
cmake --build "${SRC}/build" -j "${SLURM_CPUS_PER_TASK:-8}" --target llama-server llama-quantize
"${SRC}/build/bin/llama-quantize" --help 2>&1 | head -1 || true
"${SRC}/build/bin/llama-server" --version 2>&1 | tail -2
echo "$(date +%T) build finished"
