"""Repo-Hygiene: keine Secret-Dateien im Git-Index.

Regression: ".env 2" (macOS-Kopie mit Dev-Secrets) wurde einmal versehentlich committet.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
ERLAUBT = {".env.example"}
KEIN_CHECKOUT = shutil.which("git") is None or not (REPO / ".git").exists()


def _ist_env_datei(pfad: str) -> bool:
    name = Path(pfad).name
    return name.startswith(".env") or name.endswith(".env")


@pytest.mark.skipif(KEIN_CHECKOUT, reason="kein Git-Checkout")
def test_keine_env_dateien_getrackt() -> None:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO, capture_output=True, check=True, text=True
    ).stdout
    verdaechtig = [p for p in out.split("\0") if p and p not in ERLAUBT and _ist_env_datei(p)]
    assert verdaechtig == [], f"Secret-Dateien im Git-Index: {verdaechtig}"
