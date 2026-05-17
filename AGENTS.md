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
