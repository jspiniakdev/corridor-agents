# Working environment

The setup this project was built on, and how to recreate it.

## What's installed

| Thing | Version / choice | Why |
|---|---|---|
| Python | **3.13** via Homebrew (`python3.13`) | System Python is 3.9, which is past end-of-life and too old for `str \| None` syntax. Left untouched. |
| Env manager | **venv** (`.venv/`) | Built into Python, nothing new to learn. |
| Git identity | `Juan Spiniak` / `jspiniak.dev@gmail.com` | — |
| GitHub | **HTTPS** via `gh` credential helper | `gh auth login` chose HTTPS, so no SSH key is needed. `git push` just works. |
| GitHub account | `jspiniakdev` | Where the private repo lives. |
| Editor | VS Code | Interpreter must be set to `.venv` (Cmd+Shift+P → *Python: Select Interpreter*). |
| Assistant | Claude Code (`~/.local/bin/claude`) | Requires `~/.local/bin` on PATH. |

## Recreate it

```bash
cd ~/Projects/corridor-agents
python3.13 -m venv .venv
source .venv/bin/activate          # re-run in every new shell; prompt shows (.venv)
pip install -r requirements.txt
python -m pytest tests/ -q         # expect: 17 passed
python run.py                      # expect: agreed, Robot A goes first (correct)
```

For the LLM policies:

```bash
cp .env.example .env               # then put your key in it
source .env
python run.py --a llm --b llm
```

`.env` is gitignored and must stay that way.

## Not yet done

- `git init` / first commit / `gh repo create --private`
- Billing budget alert (before the first eval sweep — see PLAN.md Appendix B)

## Gotchas hit so far

- Plain `python3` is still 3.9. Always create the venv with `python3.13`.
- Git identity was originally set with curly quotes (`“JuanSpiniak”`), which
  embeds the quote characters in every commit. Set config values by typing in
  the terminal, not by pasting from an editor that auto-converts quotes.
- `claude` installs to `~/.local/bin`, which is not on PATH by default on macOS.
