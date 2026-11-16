#!/bin/bash -l
# GGUF export of the student (roadmap §6.3): merge the LoRA into the base (bf16), convert to
# GGUF with llama.cpp, quantize to Q4_K_M. CPU only. Needs the training env and llama.cpp
# (training/slurm/setup_llamacpp.sh). Submit from the code copy on scratch:
#   sbatch training/slurm/export_gguf.sbatch <name> <LoRA checkpoint dir>
# Writes $PRPILOT_SCRATCH/export/<name>/{merged/, <name>-bf16.gguf, <name>-Q4_K_M.gguf,
# export_meta.json}. Only export_meta.json (with every file's SHA-256) goes to ~/prpilot-keep:
# the GGUF is ~1 GB and is rebuilt from the kept adapter.
#SBATCH --job-name=prpilot-export
#SBATCH --account=eece7398.202710
#SBATCH --partition=short
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=01:00:00
#SBATCH --output=/scratch/%u/prpilot/logs/%x-%j.out

set -euo pipefail
cd "${SLURM_SUBMIT_DIR}"
source training/slurm/env.sh
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate "${PRPILOT_TRAIN_ENV}"
export HF_HUB_OFFLINE=1

NAME="$1"; ADAPTER="$2"
test -f "${ADAPTER}/adapter_model.safetensors" || { echo "no adapter in ${ADAPTER}"; exit 1; }
test -x "${LLAMACPP_DIR}/build/bin/llama-quantize" || { echo "build llama.cpp first"; exit 1; }
OUT="${PRPILOT_SCRATCH}/export/${NAME}"
BF16="${OUT}/${NAME}-bf16.gguf"; Q4="${OUT}/${NAME}-Q4_K_M.gguf"
mkdir -p "${OUT}"
echo "code commit: $(cat COMMIT 2>/dev/null || echo unknown)"

python training/sft/merge_lora.py --adapter "${ADAPTER}" --out "${OUT}/merged" \
  --model "${BASE_REPO}" --revision "${BASE_REVISION}"
# The converter ships with llama.cpp; its gguf package is the one in the same release.
PYTHONPATH="${LLAMACPP_DIR}/gguf-py" python "${LLAMACPP_DIR}/convert_hf_to_gguf.py" \
  "${OUT}/merged" --outtype bf16 --outfile "${BF16}"
"${LLAMACPP_DIR}/build/bin/llama-quantize" "${BF16}" "${Q4}" Q4_K_M

python - "${OUT}" "${NAME}" <<'EOF'
import hashlib, json, os, sys
from datetime import UTC, datetime
out, name = sys.argv[1], sys.argv[2]
def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 24), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()
files = {k: f"{out}/{name}-{k}.gguf" for k in ("bf16", "Q4_K_M")}
meta = {
    "name": name,
    "merge": json.load(open(f"{out}/merged/merge_meta.json")),
    "llamacpp": {"version": os.environ["LLAMACPP_VERSION"],
                 "tarball_sha256": open(os.path.expanduser("~/prpilot-keep/llamacpp.sha256")).read().strip()},
    "files": {k: {"path": p, "bytes": os.path.getsize(p), "sha256": sha(p)} for k, p in files.items()},
    "quantization": "Q4_K_M",
    "code_commit": open("COMMIT").read().strip() if os.path.exists("COMMIT") else None,
    "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
    "timestamp_utc": datetime.now(UTC).isoformat(timespec="seconds"),
}
json.dump(meta, open(f"{out}/export_meta.json", "w"), indent=2)
print(json.dumps(meta["files"], indent=2))
EOF
cp "${OUT}/export_meta.json" "${PRPILOT_KEEP}/export_meta_${NAME}.json"
echo "copied export_meta.json to ${PRPILOT_KEEP}"
