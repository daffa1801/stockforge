"""Log pekerjaan: catatan append-only per hari (JSONL) + pembaca untuk dashboard."""

import json
import os
import threading
import time
from typing import Any, Dict, List

from app.config import ROOT

LOG_DIR = os.path.join(ROOT, "logs")
_lock = threading.Lock()


def _ensure() -> None:
    os.makedirs(LOG_DIR, exist_ok=True)


def _path(day: str) -> str:
    return os.path.join(LOG_DIR, f"worklog-{day}.jsonl")


def log(event: str, message: str = "", **fields: Any) -> None:
    """Tulis satu kejadian. Tidak pernah melempar error ke pemanggil."""
    try:
        _ensure()
        now = time.time()
        entry = {
            "ts": round(now, 2),
            "date": time.strftime("%Y-%m-%d", time.localtime(now)),
            "time": time.strftime("%H:%M:%S", time.localtime(now)),
            "event": event,
            "message": message,
        }
        for k, v in fields.items():
            if v not in (None, ""):
                entry[k] = v
        line = json.dumps(entry, ensure_ascii=False)
        with _lock:
            with open(_path(entry["date"]), "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception as e:  # logging tidak boleh menghentikan pipeline
        print(f"[LOG] gagal menulis log: {e}")


def days() -> List[str]:
    _ensure()
    out = []
    for name in os.listdir(LOG_DIR):
        if name.startswith("worklog-") and name.endswith(".jsonl"):
            out.append(name[len("worklog-") : -len(".jsonl")])
    return sorted(out, reverse=True)


def read(day: str = "", limit: int = 300) -> List[Dict[str, Any]]:
    """Ambil log hari tertentu (default hari ini), terbaru di atas."""
    _ensure()
    target_day = day or time.strftime("%Y-%m-%d")
    path = _path(target_day)
    if not os.path.exists(path):
        return []
    entries: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except Exception:
                continue
    return list(reversed(entries[-limit:]))


def summarize(day: str = "") -> Dict[str, Any]:
    """Ringkasan harian untuk ditampilkan di kepala panel log."""
    entries = read(day, limit=10_000)
    counts: Dict[str, int] = {}
    for e in entries:
        counts[e.get("event", "?")] = counts.get(e.get("event", "?"), 0) + 1
    return {
        "day": day or time.strftime("%Y-%m-%d"),
        "total": len(entries),
        "events": counts,
        "days": days(),
    }
