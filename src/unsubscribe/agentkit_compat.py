"""Single seam for email-digest's agentkit dependency.

agentkit evolves separately from this repo. The browser helpers used here have been
stable for a long time, but the driver prefetch is an optional optimization added in
agentkit 0.4.1. Importing it unconditionally made every test fail to collect against
an older vendored agentkit, so optional additions go through a guarded import and
degrade to a no-op instead of breaking the app.
"""

from __future__ import annotations

from typing import Any

from agentkit.browser import (
    build_chrome_options_for_remote_debugging,
    chrome_driver_attach,
    ensure_brave_running,
)

try:  # agentkit >= 0.4.1
    from agentkit.browser import prefetch_chromedriver as _prefetch_chromedriver
except ImportError:  # older agentkit: the prefetch is an optimization, not a requirement
    _prefetch_chromedriver = None


def prefetch_chromedriver(
    *, debugger_address: str | None = None, binary: str | None = None, **kwargs: Any
) -> Any:
    """Start an agentkit driver prefetch when the installed agentkit supports it.

    Returns the agentkit prefetch handle, or ``None`` when this agentkit predates
    the API. Callers treat the prefetch as best-effort either way.
    """
    if _prefetch_chromedriver is None:
        return None
    return _prefetch_chromedriver(
        debugger_address=debugger_address, binary=binary, **kwargs
    )


__all__ = [
    "build_chrome_options_for_remote_debugging",
    "chrome_driver_attach",
    "ensure_brave_running",
    "prefetch_chromedriver",
]
