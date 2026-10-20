"""Render public guides using operator configuration, never request headers."""
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

PLACEHOLDER = "https://simulation.example.invalid"


def public_url():
    value = os.environ.get("QSIM_PUBLIC_URL", PLACEHOLDER).rstrip("/")
    # Restrict the origin to characters safe in HTML, Markdown, Python and shell
    # examples. Reject credentials, paths, queries and executable syntax.
    if not re.fullmatch(r"https?://[A-Za-z0-9.-]+(?::[0-9]+)?", value):
        raise ValueError("QSIM_PUBLIC_URL must be an HTTP(S) origin")
    parsed = urlsplit(value)
    if not parsed.hostname or parsed.port == 0:
        raise ValueError("QSIM_PUBLIC_URL has an invalid host or port")
    return value


def render(name):
    return (Path(__file__).parent / "static" / name).read_text().replace(
        PLACEHOLDER, public_url())
