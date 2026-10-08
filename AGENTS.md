# email-digest

Run `mamba run -n email-digest python -m email_digest digest <command>` or just ask me directly. I'll run the right commands for you — dry-runs, sources, full runs, cost reports, whatever you need.

Available topics: `ai`, `health` (defined in `topics/*.yaml` with sender lists, keywords, and model aliases).

## Commands

| Task | TUI slash command |
|------|-------------------|
| Dry-run a topic | `/digest run <topic> --dry-run` |
| Dry-run all topics | `/digest run --all --dry-run` |
| Review digest sources (interactive) | `/digest sources <topic> [--new] [--body]` |
| Full run (extract + HTML) | `/digest run <topic>` |
| Cost report | `/digest cost --json` |
| Validate topics | `/digest topics --strict` |
| Unsubscribe check (4-week window) | `/unsubscribe --days 28` |
| Regenerate OAuth token | `/unsubscribe reauth` |

## Browser environment

### Invariant: the chromedriver must match Brave's Chromium major version

The unsubscribe flow attaches to Brave via `--remote-debugging-port=9222`. The chromedriver that drives the session must share Brave's embedded Chromium major version (155 against 155). `chrome_driver_attach` enforces this: it detects the browser major (CDP first, then the installed binary), then

1. reuses a matching driver already in `~/.cache/selenium/chromedriver/`, otherwise
2. downloads the matching driver from [Chrome for Testing](https://googlechromelabs.github.io/chrome-for-testing/), bounded at 600 seconds (`AGENTKIT_DRIVER_DOWNLOAD_TIMEOUT_S` overrides it) and serialized across runs by a cache lock.

The chromedriver in PATH is ignored, so it does not need to be kept in sync. The brew-managed driver lags Brave and is not used.

**The fix runs in the background.** A Brave update is absorbed in two places, so an interactive run rarely waits:

- **In-run prefetch**: `run_check` starts `prefetch_chromedriver` before the Gmail scan, so the download overlaps work the run does anyway.
- **Daily prewarm**: the `com.email-digest.chromedriver-prewarm` LaunchAgent runs `python -m agentkit.browser.prewarm` at 10:00 daily, reading the installed Brave version so the cache is warm before the next run. Install and uninstall commands are in the header of `scripts/com.email-digest.chromedriver-prewarm.plist`.

**Check the running version:**
```bash
# Brave's Chromium version; the leading "155" is the major the driver must share
"/Applications/Brave Browser.app/Contents/MacOS/Brave Browser" --version
```

If a download is blocked, the run fails with a clear message instead of hanging. To clear it, connect to the network and retry, or drop a matching binary at `~/.cache/selenium/chromedriver/<platform>/<version>/chromedriver`.

**Where the coupling lives:**
- `agentkit/src/agentkit/browser/_driver.py` — detection, cache lookup, the locked download, and `prefetch_chromedriver`
- `agentkit/src/agentkit/browser/_browser.py` — `chrome_driver_attach` attaches with the resolved driver
- `~/.cache/selenium/chromedriver/` — where the matching driver is cached between runs
