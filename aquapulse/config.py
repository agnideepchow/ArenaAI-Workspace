"""Tiny .env loader (no extra dependency). Real environment variables win over .env."""
import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def load_env(path: pathlib.Path = ROOT / ".env") -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def get(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


load_env()
