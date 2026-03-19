#!/usr/bin/env python3
"""
BackupManager v2.0 - Linux Backup Tool
Usage: python3 backupmanager.py [--daemon] [--port 7842] [--run-job JOB_ID]
No extra dependencies required (stdlib only).
"""

import os, sys, json, shutil, hashlib, logging, argparse, fnmatch, time, subprocess
from pathlib import Path
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# ── Paths ──────────────────────────────────────────────────────────────────
CONFIG_FILE = os.path.expanduser("~/.config/backupmanager/config.json")
DEFAULT_LOG  = os.path.expanduser("~/.local/share/backupmanager/backup.log")

os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
os.makedirs(os.path.dirname(DEFAULT_LOG),  exist_ok=True)

# ── Config ─────────────────────────────────────────────────────────────────
DEFAULT_CONFIG = {
    "jobs": [],
    "settings": {
        "log_path": DEFAULT_LOG,
        "log_max_lines": 500,
        "language": "nl",
        "theme": "dark"
    },
    "version": "2.0"
}

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            if "settings" not in cfg:
                cfg["settings"] = DEFAULT_CONFIG["settings"].copy()
            return cfg
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()

def save_config(config):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

def get_log_file():
    cfg = load_config()
    return cfg.get("settings", {}).get("log_path", DEFAULT_LOG)

# ── Logging ────────────────────────────────────────────────────────────────
_log_handlers = []

def setup_logging():
    global _log_handlers
    log_file = get_log_file()
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for h in _log_handlers:
        root.removeHandler(h)
    _log_handlers = []
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(fh)
    root.addHandler(sh)
    _log_handlers = [fh, sh]

log = logging.getLogger("backupmanager")

# ── Helpers ────────────────────────────────────────────────────────────────
def file_hash(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

def is_excluded(rel_path, patterns):
    if not patterns:
        return False
    name = os.path.basename(rel_path)
    for pat in patterns:
        pat = pat.strip()
        if not pat:
            continue
        if fnmatch.fnmatch(name, pat):
            return True
        if fnmatch.fnmatch(rel_path, pat):
            return True
        parts = rel_path.replace("\\", "/").split("/")
        for part in parts:
            if fnmatch.fnmatch(part, pat):
                return True
    return False

# ── Core backup engine ─────────────────────────────────────────────────────
def run_backup(job, dry_run=False):
    src          = job["source"]
    dst          = job["destination"]
    on_missing   = job.get("on_missing",  "skip")
    on_duplicate = job.get("on_duplicate","overwrite_if_newer")
    excludes     = job.get("excludes", [])
    lang         = load_config().get("settings", {}).get("language", "nl")
    NL           = lang == "nl"

    results = {
        "copied": 0, "skipped": 0, "deleted": 0, "excluded": 0,
        "errors": [], "log": [], "started": datetime.now().isoformat(),
        "dry_run": dry_run
    }

    def L(nl, en): return nl if NL else en

    if not os.path.exists(src):
        msg = L(f"Bronpad bestaat niet: {src}", f"Source path does not exist: {src}")
        log.error(msg); results["errors"].append(msg); return results

    if not dry_run:
        os.makedirs(dst, exist_ok=True)

    src_files = {}
    if os.path.isfile(src):
        src_files[os.path.basename(src)] = src
    else:
        for root_dir, dirs, files in os.walk(src):
            dirs[:] = [d for d in dirs
                       if not is_excluded(os.path.relpath(os.path.join(root_dir, d), src), excludes)]
            for fname in files:
                abs_path = os.path.join(root_dir, fname)
                rel_path = os.path.relpath(abs_path, src)
                if is_excluded(rel_path, excludes):
                    results["excluded"] += 1
                    results["log"].append(L(f"[uitgesloten] {rel_path}", f"[excluded] {rel_path}"))
                    continue
                src_files[rel_path] = abs_path

    for rel, abs_src in src_files.items():
        abs_dst = os.path.join(dst, rel)
        try:
            if os.path.exists(abs_dst):
                if on_duplicate == "skip":
                    results["skipped"] += 1
                    results["log"].append(L(f"[overgeslagen] {rel}", f"[skipped] {rel}"))
                    continue
                elif on_duplicate == "overwrite_if_newer":
                    if os.path.getmtime(abs_src) <= os.path.getmtime(abs_dst):
                        results["skipped"] += 1
                        results["log"].append(L(f"[ongewijzigd] {rel}", f"[unchanged] {rel}"))
                        continue
                elif on_duplicate == "overwrite_if_different":
                    if file_hash(abs_src) == file_hash(abs_dst):
                        results["skipped"] += 1
                        results["log"].append(L(f"[identiek] {rel}", f"[identical] {rel}"))
                        continue

            if not dry_run:
                os.makedirs(os.path.dirname(abs_dst), exist_ok=True)
                shutil.copy2(abs_src, abs_dst)
            results["copied"] += 1
            pfx = "[DRY-RUN] " if dry_run else ""
            results["log"].append(L(f"{pfx}[gekopieerd] {rel}", f"{pfx}[copied] {rel}"))
        except Exception as e:
            msg = L(f"Fout bij {rel}: {e}", f"Error on {rel}: {e}")
            results["errors"].append(msg)
            results["log"].append(L(f"[FOUT] {rel}: {e}", f"[ERROR] {rel}: {e}"))

    if on_missing == "delete_in_dst" and os.path.isdir(dst):
        for root_dir, dirs, files in os.walk(dst):
            for fname in files:
                abs_dst_f = os.path.join(root_dir, fname)
                rel = os.path.relpath(abs_dst_f, dst)
                if rel not in src_files:
                    try:
                        if not dry_run:
                            os.remove(abs_dst_f)
                        results["deleted"] += 1
                        pfx = "[DRY-RUN] " if dry_run else ""
                        results["log"].append(L(f"{pfx}[verwijderd] {rel}", f"{pfx}[deleted] {rel}"))
                    except Exception as e:
                        results["errors"].append(L(f"Verwijderen mislukt {rel}: {e}",
                                                   f"Delete failed {rel}: {e}"))

    results["finished"] = datetime.now().isoformat()
    tag = L("TEST-RUN KLAAR" if dry_run else "BACKUP KLAAR",
            "DRY-RUN DONE"  if dry_run else "BACKUP DONE")
    log.info(f"{tag}: {job['name']} | copied={results['copied']} skipped={results['skipped']} "
             f"deleted={results['deleted']} excluded={results['excluded']} errors={len(results['errors'])}")
    return results

# ── Cron ───────────────────────────────────────────────────────────────────
def install_cron(job):
    sched   = job.get("schedule", {})
    script  = os.path.abspath(__file__)
    job_id  = job["id"]
    log_f   = get_log_file()
    m, h, d, mo, wd = (sched.get(k, v) for k, v in
                       [("minute","0"),("hour","2"),("day","*"),("month","*"),("weekday","*")])
    cmd = f'{m} {h} {d} {mo} {wd} python3 "{script}" --run-job {job_id} >> "{log_f}" 2>&1'
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    lines  = [l for l in (result.stdout if result.returncode == 0 else "").splitlines()
              if f"--run-job {job_id}" not in l]
    lines.append(cmd)
    proc = subprocess.run(["crontab", "-"], input="\n".join(lines)+"\n", text=True, capture_output=True)
    return proc.returncode == 0

def remove_cron(job_id):
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if result.returncode != 0: return True
    lines = [l for l in result.stdout.splitlines() if f"--run-job {job_id}" not in l]
    proc  = subprocess.run(["crontab", "-"], input="\n".join(lines)+"\n", text=True, capture_output=True)
    return proc.returncode == 0

# ── HTTP API ───────────────────────────────────────────────────────────────
class BackupHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args): pass

    def send_json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        config = load_config()

        if parsed.path == "/api/jobs":
            self.send_json({"jobs": config["jobs"]})

        elif parsed.path == "/api/settings":
            self.send_json({"settings": config.get("settings", {})})

        elif parsed.path == "/api/log":
            log_file = get_log_file()
            qs       = parse_qs(parsed.query)
            lines_n  = int(qs.get("lines", ["200"])[0])
            level    = qs.get("level", ["ALL"])[0].upper()
            search   = qs.get("search", [""])[0].lower()
            try:
                with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                    all_lines = f.readlines()
                filtered = []
                for l in all_lines:
                    if level != "ALL" and f"[{level}]" not in l: continue
                    if search and search not in l.lower(): continue
                    filtered.append(l)
                self.send_json({
                    "log": "".join(filtered[-lines_n:]),
                    "total_lines": len(all_lines),
                    "filtered_lines": len(filtered),
                    "log_path": log_file
                })
            except FileNotFoundError:
                self.send_json({"log": "", "total_lines": 0, "filtered_lines": 0, "log_path": log_file})

        elif parsed.path == "/api/export":
            self.send_json({"jobs": config["jobs"],
                            "exported": datetime.now().isoformat(),
                            "version": config.get("version", "2.0")})

        elif parsed.path == "/api/status":
            self.send_json({"status": "ok", "config": CONFIG_FILE,
                            "log": get_log_file(), "version": "2.0"})
        else:
            self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length) if length else b"{}"
        try:    data = json.loads(body)
        except: data = {}
        config = load_config()

        if parsed.path == "/api/jobs":
            job = {
                "id":          str(int(time.time() * 1000)),
                "name":        data.get("name", "Unnamed"),
                "source":      data.get("source", ""),
                "destination": data.get("destination", ""),
                "on_missing":  data.get("on_missing",  "skip"),
                "on_duplicate":data.get("on_duplicate","overwrite_if_newer"),
                "excludes":    data.get("excludes", []),
                "schedule":    data.get("schedule", {}),
                "enabled":     data.get("enabled", True),
                "last_run":    None,
                "created":     datetime.now().isoformat()
            }
            config["jobs"].append(job)
            save_config(config)
            if job["schedule"] and job["enabled"]:
                install_cron(job)
            self.send_json({"ok": True, "job": job})

        elif parsed.path.startswith("/api/jobs/") and parsed.path.endswith("/run"):
            job_id = parsed.path.split("/")[3]
            dry    = data.get("dry_run", False)
            job    = next((j for j in config["jobs"] if j["id"] == job_id), None)
            if not job: return self.send_json({"error": "job not found"}, 404)
            results = run_backup(job, dry_run=dry)
            if not dry:
                job["last_run"] = datetime.now().isoformat()
                save_config(config)
            self.send_json(results)

        elif parsed.path.startswith("/api/jobs/") and parsed.path.endswith("/update"):
            job_id = parsed.path.split("/")[3]
            job    = next((j for j in config["jobs"] if j["id"] == job_id), None)
            if not job: return self.send_json({"error": "job not found"}, 404)
            for k in ["name","source","destination","on_missing","on_duplicate","excludes","schedule","enabled"]:
                if k in data: job[k] = data[k]
            save_config(config)
            if job.get("schedule") and job.get("enabled"): install_cron(job)
            else: remove_cron(job_id)
            self.send_json({"ok": True, "job": job})

        elif parsed.path == "/api/settings":
            settings = config.setdefault("settings", DEFAULT_CONFIG["settings"].copy())
            for k in ["log_path", "log_max_lines", "language", "theme"]:
                if k in data: settings[k] = data[k]
            save_config(config)
            setup_logging()
            self.send_json({"ok": True, "settings": settings})

        elif parsed.path == "/api/import":
            imported_jobs = data.get("jobs", [])
            count = 0
            for job in imported_jobs:
                if not isinstance(job, dict): continue
                job["id"]      = str(int(time.time() * 1000)) + str(count)
                job.setdefault("excludes", [])
                job.setdefault("created", datetime.now().isoformat())
                job["last_run"] = None
                config["jobs"].append(job)
                count += 1
            save_config(config)
            self.send_json({"ok": True, "imported": count})

        elif parsed.path == "/api/log/clear":
            log_file = get_log_file()
            try:
                open(log_file, "w").close()
                self.send_json({"ok": True})
            except Exception as e:
                self.send_json({"error": str(e)}, 500)

        else:
            self.send_json({"error": "not found"}, 404)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/jobs/"):
            job_id = parsed.path.split("/")[3]
            config = load_config()
            config["jobs"] = [j for j in config["jobs"] if j["id"] != job_id]
            save_config(config)
            remove_cron(job_id)
            self.send_json({"ok": True})
        else:
            self.send_json({"error": "not found"}, 404)

# ── Entry point ────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="BackupManager v2.0")
    parser.add_argument("--daemon",  action="store_true")
    parser.add_argument("--port",    type=int, default=7842)
    parser.add_argument("--run-job", metavar="JOB_ID")
    args = parser.parse_args()

    setup_logging()

    if args.run_job:
        config = load_config()
        job = next((j for j in config["jobs"] if j["id"] == args.run_job), None)
        if not job:
            log.error(f"Job {args.run_job} not found")
            sys.exit(1)
        results = run_backup(job)
        job["last_run"] = datetime.now().isoformat()
        save_config(config)
        sys.exit(0 if not results["errors"] else 1)

    elif args.daemon:
        server = HTTPServer(("127.0.0.1", args.port), BackupHandler)
        log.info(f"BackupManager v2.0 started on http://127.0.0.1:{args.port}")
        log.info(f"Config : {CONFIG_FILE}")
        log.info(f"Log    : {get_log_file()}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            log.info("Server stopped")
    else:
        print("BackupManager v2.0")
        print("  python3 backupmanager.py --daemon           # Start API server")
        print("  python3 backupmanager.py --run-job JOB_ID  # Run a specific job")
        print(f"\nConfig : {CONFIG_FILE}")
        print(f"Log    : {DEFAULT_LOG}")

if __name__ == "__main__":
    main()
