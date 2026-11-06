"""The only way a personal agent reaches a model.

The model gets no tools, no MCP servers and no project or user settings. It cannot open a
file or call Switch. It only ever sees the text the runner hands it, and its output is
treated as untrusted data until a validator accepts it. Sending that prompt to the model
provider is a real disclosure, documented in README and docs/PRIVACY.md.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile

MODEL = os.environ.get("KITTY_MODEL", "haiku")
TIMEOUT_SECONDS = 180


class LlmError(RuntimeError):
    pass


def run_argv(system: str, prompt: str, model: str = MODEL) -> list[str]:
    return [
        "claude", "-p", prompt,
        "--model", model,
        "--system-prompt", system,
        "--tools", "",
        "--strict-mcp-config",
        "--setting-sources", "",
        "--no-session-persistence",
    ]


def complete(system: str, prompt: str, model: str = MODEL) -> str:
    """Run one stateless completion from an empty directory and return its text."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "ANTHROPIC_BASE"))}
    with tempfile.TemporaryDirectory(prefix="kitty-llm-") as cwd:
        argv = run_argv(system, prompt, model) + ["--output-format", "json"]
        p = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=TIMEOUT_SECONDS, stdin=subprocess.DEVNULL)
    if p.returncode != 0:
        raise LlmError(f"claude exited {p.returncode}: {(p.stderr or p.stdout)[:300]}")
    out = json.loads(p.stdout)
    if out.get("is_error"):
        raise LlmError(f"model error: {str(out.get('result'))[:300]}")
    return out["result"]
