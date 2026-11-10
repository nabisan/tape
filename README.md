# tape

Bar-level US equity backtesting service. Design: [ARCHITECTURE.md](ARCHITECTURE.md).

## Setup

```bash
uv sync
uv run tape env
```

## Checks

```bash
uv run ruff format --check && uv run ruff check && uv run mypy src tests && uv run pytest
```

## Run

```bash
uv run tape catalog build
uv run tape serve api
uv run tape serve ui
```

The API and UI bind to `127.0.0.1`. From a laptop, reach a server's UI through a tunnel:

```bash
ssh -N -L 8501:127.0.0.1:8501 <ssh-host>
```

## Configuration

| Variable | Default |
|---|---|
| `TAPE_HOME` | `~/tape` |
| `TAPE_SOURCE_DIR` | `$TAPE_HOME/source` |
| `TAPE_STORE_DIR` | `$TAPE_HOME/store` |
| `TAPE_RESULTS_DIR` | `$TAPE_HOME/results` |
| `TAPE_LOG_LEVEL` | `INFO` (`DEBUG`, `INFO`, `WARN`, `ERROR`) |

## Deploy

```bash
deploy/sync.sh <ssh-host>
```

Copies the working tree to `~/tape/app` on the host, installs the locked environment and runs `tape env` there.
