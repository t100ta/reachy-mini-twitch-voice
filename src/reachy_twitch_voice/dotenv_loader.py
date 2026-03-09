from __future__ import annotations

from pathlib import Path


def load_env_file(path: str, overwrite: bool = False) -> bool:
    """Load KEY=VALUE lines from a local env file into process env.

    Returns True when the file exists and was read, False otherwise.
    """
    import os

    expanded = os.path.expandvars(os.path.expanduser(path))
    p = Path(expanded)
    if not p.exists() or not p.is_file():
        return False

    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key:
            continue
        if overwrite or key not in os.environ:
            os.environ[key] = value

    return True
