import os
import sys
from pathlib import Path

os.environ["LLM_MODE"] = "mock"          # tests never call real providers
os.environ.pop("MOCK_INJECT_ERROR", None)
# tests must never write into the real runs/ and logs/ folders
import tempfile  # noqa: E402

_tmp = tempfile.mkdtemp(prefix="safetytrace_tests_")
os.environ["SAFETYTRACE_RUNS_DIR"] = os.path.join(_tmp, "runs")
os.environ["SAFETYTRACE_LOGS_DIR"] = os.path.join(_tmp, "logs")
os.environ["QUOTA_STATE_FILE"] = os.path.join(_tmp, "logs", "quota_state.json")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
