"""The agentkit compatibility seam tolerates an older installed agentkit."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from unsubscribe import agentkit_compat


def test_required_browser_helpers_are_reexported() -> None:
    assert callable(agentkit_compat.chrome_driver_attach)
    assert callable(agentkit_compat.ensure_brave_running)
    assert callable(agentkit_compat.build_chrome_options_for_remote_debugging)


def test_prefetch_delegates_when_available() -> None:
    delegate = MagicMock(return_value="handle")
    with patch.object(agentkit_compat, "_prefetch_chromedriver", delegate):
        result = agentkit_compat.prefetch_chromedriver(
            debugger_address="127.0.0.1:9222"
        )

    assert result == "handle"
    delegate.assert_called_once_with(
        debugger_address="127.0.0.1:9222", binary=None
    )


def test_prefetch_is_noop_when_agentkit_predates_it() -> None:
    with patch.object(agentkit_compat, "_prefetch_chromedriver", None):
        assert (
            agentkit_compat.prefetch_chromedriver(
                debugger_address="127.0.0.1:9222"
            )
            is None
        )
