"""The web interface: one self-contained page, no build step, no CDN.

The page lives in ``webui.html`` next to this module rather than in a string
here. That is deliberate: it is HTML, CSS and JavaScript, and keeping it in a
real file means editors highlight it, nothing has to be escaped, and a stray
triple quote in the markup cannot break the Python module.

It is inlined in one document on purpose - no framework, no font, no CDN. The
service is meant to live on a LAN with no outbound dependencies, so loading
anything from somebody else's server would be pointless and a privacy leak.
"""

from __future__ import annotations

from pathlib import Path

from . import GITHUB_URL, NAME, TAGLINE, __version__

ASSET = Path(__file__).with_name("webui.html")

_cache: dict = {"mtime": None, "html": ""}


def _source() -> str:
    """Read the asset, re-reading only when it changes (edits need no restart)."""
    try:
        stamp = ASSET.stat().st_mtime
    except OSError:
        return (
            "<!DOCTYPE html><html><body style='font-family:system-ui;padding:2rem'>"
            "<h1>Web interface unavailable</h1><p>This installation is missing its page "
            "asset: <code>" + str(ASSET) + "</code>. Reinstall the package.</p></body></html>"
        )
    if _cache["mtime"] != stamp:
        _cache["html"] = ASSET.read_text(encoding="utf-8")
        _cache["mtime"] = stamp
    return str(_cache["html"])


def page() -> str:
    """Render the single page for this build."""
    return (_source()
            .replace("__NAME__", NAME)
            .replace("__TAGLINE__", TAGLINE)
            .replace("__VERSION__", __version__)
            .replace("__REPO__", GITHUB_URL))
