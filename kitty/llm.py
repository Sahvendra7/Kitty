"""The only way a personal agent reaches a model.

The model gets no tools, no MCP servers and no project or user settings. It cannot open a
file or call Switch. It only ever sees the text the runner hands it, and its output is
treated as untrusted data until a validator accepts it. Sending that prompt to the model
provider is a real disclosure, documented in README and docs/PRIVACY.md.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile

MODEL = os.environ.get("KITTY_MODEL", "claude-haiku-5-5")
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
    # A failed call's output could quote the prompt, which holds statement lines, so only a code gets logged.
    if p.returncode != 0:
        code = re.search(r"\[claude-code:([a-z_]+)\]", p.stderr or p.stdout or "")
        raise LlmError(f"claude exited {p.returncode}" + (f": {code.group(1)}" if code else ""))
    out = json.loads(p.stdout)
    if out.get("is_error"):
        raise LlmError("the model reported an error")
    return out["result"]
