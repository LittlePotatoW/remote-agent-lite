# Contributing to remote-agent-lite

Thanks for taking the time to contribute. Issues, bug reports, feature ideas and pull requests are all welcome — in English or in Chinese.

## Before you start

- For anything bigger than a small fix, open an issue first so we can agree on the approach before you spend time on it.
- Search the existing issues and pull requests — the answer may already be there.
- One topic per pull request. Small, focused PRs get reviewed (and merged) much faster than large mixed ones.

## Repository layout

- `remote-agent-server/` — the server: FastAPI backend, Svelte frontend, deployment scripts and tests.
- Other clients live in sibling directories (an Android app is planned). Each end is self-contained and talks to the server only over its HTTP API.

## Development setup

```bash
cd remote-agent-server

# backend
python -m venv .venv
. .venv/bin/activate          # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt

# frontend
cd frontend
npm install
```

Run the backend directly while developing the frontend:

```bash
uvicorn remote_agent_lite.main:app --app-dir backend --reload --port 8080
cd frontend && npm run dev
```

## Install the git hooks first

```bash
bash .githooks/install.sh          # sets core.hooksPath for this clone
bash .githooks/install.sh --check  # show current state
```

The hooks are part of the workflow, not decoration. They reject commits that add binaries, secrets (`.env`, `*.pem`, `*.key`), dependencies (`.venv`, `node_modules`, `__pycache__`), build output (`dist`, `build`), databases, archives and media, or files larger than 1 MiB — and they also check whether your branch is behind the remote main branch, merging it in when it is. Run `bash .githooks/run-tests.sh` to self-test the hooks.

## Tests

Please make sure these pass before opening a pull request:

```bash
cd remote-agent-server
python -m pytest -q               # backend, 55 tests

cd frontend
npm run check                     # svelte-check, must be 0 errors
npm run build                     # production build must succeed
```

## Commit messages

This repository uses conventional-commit style prefixes, so the history stays readable:

| Prefix | Use for |
| --- | --- |
| `feat:` | a new feature |
| `fix:` | a bug fix |
| `chore:` | tooling, dependencies, housekeeping |
| `docs:` | documentation only |
| `style:` | formatting or visual tweaks with no behaviour change |
| `refactor:` | restructuring without changing behaviour |
| `test:` | tests only |

Write the subject in the imperative mood and keep it under ~72 characters; explain *why* in the body when it is not obvious.

## Pull requests

1. Fork the repository and create a branch, for example `fix/session-pinning` or `feat/android-client`.
2. Make the change, run the tests above, and commit.
3. Open the pull request against `main` and describe what changed and why. Include a screenshot or short clip for UI changes, and mention anything you could not test.
4. Keep the discussion in the PR; push follow-up commits rather than force-pushing over review comments.

## Reporting bugs

A useful report contains: what you did, what you expected, what actually happened, the server and client versions (OS, Python, Node), and the relevant log lines — for example `journalctl -u remote-agent-lite -n 100 --no-pager`.

## Security

remote-agent-lite is a self-hosted tool with a single admin password and no sandbox around the agent, so a compromised instance means a compromised server. Please do not open a public issue for a security problem — contact the maintainer privately (a GitHub security advisory or a direct message) and give us a chance to fix it first.

## License

By contributing you agree that your contributions are licensed under the MIT License — see [LICENSE](LICENSE).
