# Homework — post-implementation manual steps

## 1. Verify health topic extraction via OpenCode Go
```bash
mamba run -n email-digest python -m email_digest digest run health --dry-run
```
Check extraction quality. Model: `deepseek-v4-flash` (alias `fast`), endpoint: `https://opencode.ai/zen/go/v1`, key auto-read from `~/.local/share/opencode/auth.json` (`opencode-go` block). The former `cheap`/MiniMax alias was removed — MiniMax M2.5 is gone from the Go endpoint, and M2.7/M3 require the `/messages` protocol.

## 2. Tune topic keywords
Edit `topics/health.yaml` and `topics/ai.yaml`. Current keywords are examples — replace with terms that match your real inbox.

## 3. Migrate decisionmaker agent
In `/Users/chaehan/Software/Prototypes/decisionmaker`:
```bash
mkdir -p .opencode/agents
mv prompts/decisionmaker.md .opencode/agents/decisionmaker.md
ln -s $(pwd)/.opencode/agents/decisionmaker.md ~/.config/opencode/agents/decisionmaker.md
```
Then delete `opencode_sync.py` (its only job was the symlink).

## 4. Set up cron
Copy `scripts/digest-cron.example.sh`, set env vars, add to launchd or cron.

## 5. Replace topic senders with real addresses
`topics/health.yaml` and `topics/ai.yaml` still use example.com senders. Replace with actual From addresses you receive.
