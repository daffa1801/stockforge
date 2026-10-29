"""Pembaca spreadsheet: ambil baris prompt, kelompokkan per konsep/event."""

import io
import csv
import re
import urllib.parse
import urllib.request
from typing import Any, Dict, List

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def extract_sheet_id(url: str) -> str:
    url = (url or "").strip()
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9\-_]+)", url)
    if m:
        return m.group(1)
    if re.match(r"^[a-zA-Z0-9\-_]{20,}$", url):
        return url
    raise ValueError("Link spreadsheet tidak dikenali.")


def _gviz_url(sheet_id: str, sheet_name: str) -> str:
    return (
        f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq"
        f"?tqx=out:csv&sheet={urllib.parse.quote(sheet_name)}"
    )


def _export_url(sheet_id: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.read().decode("utf-8-sig", errors="replace")


def fetch_csv_text(sheet_url: str, sheet_name: str = "") -> str:
    """Coba gviz (nama sheet), lalu fallback ke ekspor CSV sheet pertama."""
    sheet_id = extract_sheet_id(sheet_url)
    errors = []
    if sheet_name:
        try:
            text = _get(_gviz_url(sheet_id, sheet_name))
            if "," in text.split("\n", 1)[0]:
                return text
        except Exception as e:
            errors.append(f"gviz: {e}")
    try:
        return _get(_export_url(sheet_id))
    except Exception as e:
        errors.append(f"export: {e}")
    raise RuntimeError("Gagal mengambil spreadsheet. " + " | ".join(errors))


def _pick(row: Dict[str, str], *names: str) -> str:
    for n in names:
        if n in row and row[n]:
            return row[n]
    return ""


def parse_csv(csv_text: str) -> List[Dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(csv_text))
    items: List[Dict[str, Any]] = []
    for i, raw in enumerate(reader, start=1):
        row = {(k or "").strip().lstrip("\ufeff"): (v or "").strip() for k, v in raw.items() if k}
        filename = _pick(row, "Filename", "File Name", "Nama File")
        prompt = _pick(row, "AI Prompt (Siap Pakai)", "Prompt", "AI Prompt")
        if not filename or not prompt:
            continue
        items.append(
            {
                "no": _pick(row, "No") or str(i),
                "filename": filename,
                "prompt": prompt,
                "title": _pick(row, "Title", "Title (Judul Adobe Stock)"),
                "keywords": _pick(row, "Keywords", "Keywords / Tags (30 Tags Utama)"),
                "category": _pick(row, "Category", "Kategori"),
                "concept": _pick(row, "Ide Konsep", "Konsep", "Target Momen"),
            }
        )
    return items


def concept_key(item: Dict[str, Any]) -> str:
    """Kunci pengelompokan: nomor ide kalau ada, kalau tidak teks konsepnya."""
    concept = (item.get("concept") or "").strip()
    m = re.match(r"Ide\s+(\d+)", concept, re.I)
    if m:
        return f"Ide {m.group(1)}"
    if concept:
        return concept[:60]
    # fallback: 6 gambar berurutan dianggap satu konsep
    return "Tanpa Konsep"


def event_label(item: Dict[str, Any]) -> str:
    concept = (item.get("concept") or "").strip()
    return concept.split(":", 1)[1].strip() if ":" in concept else concept


def group_by_concept(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Kelompokkan berurutan sesuai urutan kemunculan di spreadsheet."""
    order: List[str] = []
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for it in items:
        key = concept_key(it)
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(it)
    return [
        {"key": k, "label": event_label(buckets[k][0]), "items": buckets[k]}
        for k in order
    ]


def load_plan(sheet_url: str, sheet_name: str = "") -> List[Dict[str, Any]]:
    return parse_csv(fetch_csv_text(sheet_url, sheet_name))
