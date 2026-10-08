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

The unsubscribe flow attaches to Brave via `--remote-debugging-port=9222`. The chromedriver that drives the session must share Brave's embedded Chromium major version (155 against 155). `chrome_driver_attach` enforces this: it reads the running browser's major from the CDP endpoint, then

1. resolves a matching driver from the Selenium cache in offline mode (about two seconds, no network), and
2. when nothing matches is cached (the first run after a Brave update), downloads the matching driver from [Chrome for Testing](https://googlechromelabs.github.io/chrome-for-testing/) into `~/.cache/selenium/`, bounded at 180 seconds.

The chromedriver in PATH is ignored (`--skip-driver-in-path`), so it does not need to be kept in sync. The brew-managed driver lags Brave and is not used.

**Check the running version:**
```bash
# Brave's Chromium version; the leading "155" is the major the driver must share
"/Applications/Brave Browser.app/Contents/MacOS/Brave Browser" --version
```

**When Brave auto-updates** and the Chromium major bumps: the first unsubscribe run downloads the matching chromedriver once and caches it; later runs reuse it. There is no manual step. If the download is blocked, the run fails with a clear message instead of hanging. To clear it, connect to the network and retry, or drop a matching binary at `~/.cache/selenium/chromedriver/<platform>/<version>/chromedriver`.

**Where the coupling lives:**
- `agentkit/src/agentkit/browser/_browser.py` — `chrome_driver_attach` reads the running browser major from CDP, resolves offline from the cache, then downloads from Chrome for Testing when no match is cached
- `~/.cache/selenium/chromedriver/` — where the matching driver is cached between runs
