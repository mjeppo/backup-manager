# BackupManager

A lightweight Linux backup tool with a web-based interface. No external dependencies — pure Python stdlib.

## Features

- Define multiple backup jobs (source → destination)
- Configurable duplicate handling: skip, overwrite if newer, overwrite if different
- Configurable missing-file handling: keep or delete from destination
- Exclude patterns (glob-based)
- Dry-run mode to preview changes without copying
- Cron scheduling per job
- Import/export jobs as JSON
- Dark and light theme
- Dutch and English interface

## Usage

**Start the API server:**
```bash
python3 backupmanager.py --daemon
```

Then open `backupmanager.html` in your browser (e.g. via `file://` or a local web server).

**Run a specific job directly (e.g. from cron):**
```bash
python3 backupmanager.py --run-job JOB_ID
```

**Options:**
```
--daemon          Start the HTTP API server
--port PORT       Port to listen on (default: 7842)
--run-job JOB_ID  Run a specific job by ID and exit
```

## Configuration

Config is stored in `~/.config/backupmanager/config.json`.
Logs are written to `~/.local/share/backupmanager/backup.log`.

## Requirements

- Python 3.6+
- Linux (uses `crontab` for scheduling)
- No pip packages required

## License

MIT
