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
| Docker | **Docker Desktop**, via `brew install --cask docker` | Phase 7 (D32) - Compose runs the fleet. `docker`/`docker compose` need Docker Desktop actually launched (the whale icon in the menu bar) before either command works, not just installed. |
| GCP CLI | **gcloud**, via `brew install --cask google-cloud-sdk` | Phase 8 (D33) - deploying to Cloud Run. Set the active account with `gcloud config set account ...` if more than one is authenticated - `gcloud auth list` shows all of them. |
| GCP project | `corridor-agents` (project number `433484676345`), under org `866252710860` | Billing already enabled. Region used throughout: `us-central1`. |
| Assistant | Claude Code (`~/.local/bin/claude`) | Requires `~/.local/bin` on PATH. |

## Recreate it

```bash
cd ~/Projects/corridor-agents
python3.13 -m venv .venv
source .venv/bin/activate          # re-run in every new shell; prompt shows (.venv)
pip install -r requirements.txt
python -m pytest tests/ -q         # expect: 54 passed
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
- `brew install --cask docker` needs an interactive terminal - it hits a
  `sudo` prompt (to symlink the `docker-compose` CLI plugin) partway through,
  and fails/rolls back entirely if run somewhere that can't prompt for a
  password. Run it directly in a real terminal, not from a non-interactive
  context. Docker Desktop also needs to actually be launched once
  (`open -a Docker`, approve the one-time permission dialog) before
  `docker`/`docker compose` work at all - installed isn't the same as running.
- The `corridor-agents` GCP project is fresh and under an organization,
  which meant several IAM grants Google used to hand out automatically on
  new projects had to be added by hand before Cloud Run deploys would work
  at all - `roles/storage.objectViewer`, `roles/logging.logWriter`, and
  `roles/artifactregistry.writer`, all on the default compute service
  account (`433484676345-compute@developer.gserviceaccount.com`). Full
  story (including how each one was actually diagnosed) in
  `docs/DECISIONS.md` D33 - worth reading before assuming a fresh GCP
  project "just works" the way it used to.
- **Vertex AI quota is 0 on a fresh project and the increase is
  auto-denied.** `anthropic-claude-sonnet` on Vertex (D34) needs a quota
  bump that Google's automated system rejects on a project with no billing
  history - support ticket open, no resolution yet. Gemini's Vertex quota
  (D36) is *not* gated, so `--policy gemini` is the working path for the
  deployed pipeline meanwhile.
- **Firestore** (D38, Phase 10b-2): the `(default)` database is created in
  `us-central1` (Native mode), same region as Cloud Run. Its location is
  **permanent** - to change it you delete and recreate. Local dev normally
  uses the emulator, but that needs a Java runtime this machine doesn't
  have (`java` is an install stub), so `--firestore` is verified straight
  against the real database instead. `roles/datastore.user` is granted to
  `robot-a@corridor-agents.iam.gserviceaccount.com` (the SA the local ADC
  impersonates); `world_server`'s own SA gets it in 10b-3.
- The local `gcloud` config drifted from this project once (was pointed at
  an unrelated `cubebackup-*` project); `gcloud config set project
  corridor-agents` fixed it. `gcloud auth application-default
  set-quota-project` fails here ("not user credentials") because the ADC
  is an impersonated service account, not a plain login - that's expected,
  not a problem.
