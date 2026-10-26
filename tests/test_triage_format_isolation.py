import subprocess
import sys


def test_triage_format_does_not_import_pydantic_ai():
    """The teacher's vLLM environment has no Pydantic AI; the shared prompt must not need it."""
    code = (
        "import sys, prpilot_core.agents.triage_format; "
        "sys.exit(1 if any(m.startswith('pydantic_ai') for m in sys.modules) else 0)"
    )
    assert subprocess.run([sys.executable, "-c", code]).returncode == 0
