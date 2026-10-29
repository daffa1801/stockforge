"""Konfigurasi StockForge - satu sumber kebenaran untuk semua setelan."""

import json
import os
from typing import Any, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "config.json")
STATE_PATH = os.path.join(ROOT, "state.json")

DEFAULT_CONFIG: Dict[str, Any] = {
    "sheet_url": "",
    "sheet_name": "Master Prompt & Ide Lengkap",
    "output_dir": "output",
    "shortlist_dir": "output/_shortlist",
    "rejected_dir": "output/_rejected",
    "profiles": [{"name": f"Akun {i}", "dir": f"profiles/akun-{i}"} for i in range(1, 9)],
    "active_profile": 0,
    "chrome_executable": "",
    "headless": False,
    "delay_between_prompts_sec": 12,
    "timeout_per_image_sec": 900,
    "max_per_concept": 3,
    "concept_limit": 0,
    "vote_threshold": 3,
    "judge_parallel": 4,
    "port": 8770,
    "router": {
        "url": "http://localhost:20128/v1/chat/completions",
        "api_key": "",
        "timeout_sec": 180,
    },
    "judges": [
        {"id": "event", "label": "Event legibility", "model": "ag/gemini-3.8-flash"},
        {"id": "sell", "label": "Kelayakan jual", "model": "1/glm-5.3-flash"},
        {"id": "tech", "label": "Teknis & compliance", "model": "1/deepseek-v4.1-flash"},
        {"id": "meta", "label": "Kecocokan metadata", "model": "ag/gemini-3.8-flash-low"},
    ],
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config() -> Dict[str, Any]:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return _deep_merge(DEFAULT_CONFIG, json.load(f))
        except Exception as e:
            print(f"[CONFIG] Gagal membaca config.json: {e}. Memakai default.")
    return json.loads(json.dumps(DEFAULT_CONFIG))


def save_config(cfg: Dict[str, Any]) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def resolve(path: str) -> str:
    """Path relatif selalu dihitung dari root proyek, bukan dari cwd."""
    if not path:
        return ROOT
    return path if os.path.isabs(path) else os.path.join(ROOT, path)


def ensure_dirs(cfg: Dict[str, Any]) -> None:
    for key in ("output_dir", "shortlist_dir", "rejected_dir"):
        os.makedirs(resolve(cfg[key]), exist_ok=True)
    for p in cfg.get("profiles", []):
        os.makedirs(resolve(p["dir"]), exist_ok=True)


def profile_dirs(cfg: Dict[str, Any]) -> List[Dict[str, str]]:
    return [
        {"name": p.get("name") or os.path.basename(p.get("dir", "")), "dir": resolve(p["dir"])}
        for p in cfg.get("profiles", [])
        if p.get("dir")
    ]
