"""Sincronizza la cartella data/ con il branch `data` del repository.

I dati cambiano ogni pochi minuti: tenerli su un branch separato evita di ricostruire il sito
dell'app (che sta su `main`) a ogni salvataggio, così possiamo pubblicarli molto più spesso.

    python -m bot.datasync pull     # all'inizio del giro: data/ <- branch data
    python -m bot.datasync push     # salva data/ -> branch data (crea il branch se manca)

Impostazioni: se l'app le ha cambiate sul branch mentre il bot girava (e il bot no), vince l'app.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRANCH = os.environ.get("DATA_BRANCH", "data")
SETTINGS = "data/settings.json"
MARK = ROOT / ".git" / "forexbot-settings-sync"


def _git(*args, check=True, env=None, input_=None) -> str:
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, env=env, input=input_)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout.strip()


def _fetch() -> bool:
    r = subprocess.run(["git", "fetch", "-q", "origin", f"+refs/heads/{BRANCH}:refs/remotes/origin/{BRANCH}"],
                       cwd=ROOT, capture_output=True, text=True)
    return r.returncode == 0


def _remote_file(path: str) -> str | None:
    r = subprocess.run(["git", "show", f"origin/{BRANCH}:{path}"], cwd=ROOT, capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def _h(s: str | None) -> str:
    return hashlib.sha1((s or "").encode()).hexdigest()


def _local_settings() -> str | None:
    p = ROOT / SETTINGS
    return p.read_text() if p.exists() else None


def pull() -> None:
    if not _fetch():
        print("datasync: branch dati non ancora creato, uso data/ di main")
        MARK.write_text(_h(_local_settings()))
        return
    main_cal = (ROOT / "data/calibration.json").read_text() if (ROOT / "data/calibration.json").exists() else None
    _git("checkout", f"origin/{BRANCH}", "--", "data")
    _git("reset", "-q", "--", "data", check=False)   # niente file in stage su main
    # calibrazione: se su main ce n'è una più recente (workflow settimanale vecchio stile), tienila
    if main_cal:
        import json
        try:
            if json.loads(main_cal).get("generated", "") > json.loads(
                    (ROOT / "data/calibration.json").read_text()).get("generated", ""):
                (ROOT / "data/calibration.json").write_text(main_cal)
        except Exception:  # noqa: BLE001
            pass
    MARK.write_text(_h(_local_settings()))
    print("datasync: dati presi dal branch", BRANCH)


def push(message: str | None = None, tries: int = 5) -> bool:
    """Pubblica data/. Restituisce True se nel frattempo l'app aveva cambiato le impostazioni
    (in quel caso data/settings.json locale viene aggiornato con la versione dell'app)."""
    message = message or f"Dati bot {time.strftime('%Y-%m-%dT%H:%MZ', time.gmtime())}"
    settings_from_app = False
    for attempt in range(tries):
        exists = _fetch()
        if exists:
            remote = _remote_file(SETTINGS)
            synced = MARK.read_text().strip() if MARK.exists() else ""
            local = _local_settings()
            if remote is not None and _h(remote) != synced and _h(local) == synced:
                (ROOT / SETTINGS).write_text(remote)      # l'app ha cambiato le impostazioni: vince l'app
                settings_from_app = True
        with tempfile.TemporaryDirectory() as td:
            env = {**os.environ, "GIT_INDEX_FILE": str(Path(td) / "index")}
            if exists:
                _git("read-tree", f"origin/{BRANCH}", env=env)
            else:
                _git("read-tree", "--empty", env=env)
            _git("rm", "-r", "-q", "-f", "--cached", "--ignore-unmatch", "data", env=env)
            _git("add", "-f", "data", env=env)
            tree = _git("write-tree", env=env)
        if exists and tree == _git("rev-parse", f"origin/{BRANCH}^{{tree}}"):
            MARK.write_text(_h(_local_settings()))
            return settings_from_app
        args = ["commit-tree", tree, "-m", message] + (["-p", f"origin/{BRANCH}"] if exists else [])
        commit = _git(*args)
        r = subprocess.run(["git", "push", "-q", "origin", f"{commit}:refs/heads/{BRANCH}"],
                           cwd=ROOT, capture_output=True, text=True)
        if r.returncode == 0:
            MARK.write_text(_h(_local_settings()))
            return settings_from_app
        print("datasync: push rifiutato, riprovo:", r.stderr.strip()[:200])
        time.sleep(2 * (attempt + 1))
    raise RuntimeError("datasync: impossibile pubblicare i dati")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "pull":
        pull()
    elif cmd == "push":
        push(" ".join(sys.argv[2:]) or None)
    else:
        print(__doc__)
        sys.exit(2)
