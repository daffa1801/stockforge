"""Panel penilai: 4 juri vision lewat 9router, satu rubrik per juri."""

import base64
import io
import json
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

RUBRICS: Dict[str, str] = {
    "event": (
        "Kamu juri EVENT READABILITY untuk stock photo musiman. "
        "Tugasmu: apakah gambar ini langsung terbaca sebagai event/tema yang dituju "
        "oleh pembeli, bukan sekadar suasana umum? Simbol harus jelas dan spesifik."
    ),
    "sell": (
        "Kamu juri KELAYAKAN JUAL stock photo. "
        "Tugasmu: apakah gambar ini berguna bagi pembeli? Nilai ruang kosong untuk teks, "
        "kekuatan subjek saat diperkecil jadi thumbnail, dan apakah layak dipakai sebagai "
        "sampul buku, poster, artwork game, atau background artikel."
    ),
    "tech": (
        "Kamu juri TEKNIS & COMPLIANCE stock photo. "
        "Tugasmu: cari cacat teknis - artefak AI, anatomi atau perspektif aneh, teks nyasar, "
        "watermark/logo, noise berlebih, blur, atau komposisi rusak. "
        "Jangan menilai tema atau selera."
    ),
    "meta": (
        "Kamu juri KECOCOKAN METADATA stock photo. "
        "Tugasmu: apakah isi gambar benar-benar sesuai dengan JUDUL dan KEYWORD yang diberikan? "
        "Kalau gambar tidak menggambarkan kata kunci utamanya, pembeli tidak akan menemukannya."
    ),
}

OUTPUT_RULE = (
    'Balas HANYA satu objek JSON tanpa penjelasan lain: '
    '{"score": <angka 0-100>, "pass": <true atau false>, "reason": "<maksimal 12 kata>"}'
)


class JudgeError(RuntimeError):
    pass


def encode_image(path: str, max_side: int = 1024, quality: int = 82) -> str:
    """Perkecil gambar jadi data URL JPEG supaya payload ke model tetap ringan."""
    from PIL import Image

    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((max_side, max_side), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=quality, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _iter_json_objects(text: str):
    """Ambil semua objek JSON dari body yang bisa berupa JSON biasa, JSON+'data: [DONE]', atau SSE."""
    dec = json.JSONDecoder()
    i = 0
    while i < len(text):
        j = text.find("{", i)
        if j < 0:
            break
        try:
            obj, end = dec.raw_decode(text, j)
        except json.JSONDecodeError:
            i = j + 1
            continue
        yield obj
        i = end


def _collect_text(raw: str) -> str:
    chunks: List[str] = []
    for obj in _iter_json_objects(raw):
        if "error" in obj and not obj.get("choices"):
            raise JudgeError(str(obj["error"])[:200])
        for ch in obj.get("choices") or []:
            msg = ch.get("message") or {}
            piece = msg.get("content") or (ch.get("delta") or {}).get("content") or ""
            if piece:
                chunks.append(piece)
    return "".join(chunks)


def extract_json(text: str) -> Dict[str, Any]:
    body = re.sub(r"```(?:json)?", "", text or "").strip()
    for obj in _iter_json_objects(body):
        if isinstance(obj, dict) and "score" in obj:
            return obj
    raise JudgeError(f"Jawaban bukan JSON: {(text or '')[:120]!r}")


def call_model(
    model: str,
    prompt: str,
    image_data_url: str,
    router: Dict[str, Any],
    max_tokens: int = 1200,
    attempts: int = 2,
) -> Dict[str, Any]:
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_data_url}},
                ],
            }
        ],
    }
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if router.get("api_key"):
        headers["Authorization"] = "Bearer " + router["api_key"]

    last_error = "unknown"
    timeout = float(router.get("timeout_sec", 120))
    for attempt in range(1, attempts + 1):
        req = urllib.request.Request(router["url"], data=data, headers=headers)
        started = time.time()
        try:
            # Streaming dibaca dengan batas waktu dinding: server 9router bisa menahan
            # koneksi tetap terbuka, jadi timeout socket saja tidak cukup.
            with urllib.request.urlopen(req, timeout=min(15.0, timeout)) as r:
                buf = []
                while time.time() - started < timeout:
                    piece = r.read(8192)
                    if not piece:
                        break
                    buf.append(piece)
                raw = b"".join(buf).decode("utf-8", errors="replace")
            text = _collect_text(raw)
            if not text.strip():
                raise JudgeError("Respons kosong dari model")
            return extract_json(text)
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"[:200]
            if time.time() - started >= timeout:
                break
            if attempt < attempts:
                time.sleep(1.5)
    raise JudgeError(last_error)


def build_prompt(judge_id: str, item: Dict[str, Any]) -> str:
    rubric = RUBRICS.get(judge_id, RUBRICS["sell"])
    parts = [rubric]
    if judge_id == "event":
        parts.append(f"EVENT YANG DITUJU: {item.get('concept') or 'tidak disebutkan'}")
    if judge_id == "meta":
        parts.append(f"JUDUL: {item.get('title') or '-'}")
        parts.append(f"KEYWORD: {item.get('keywords') or '-'}")
    parts.append(OUTPUT_RULE)
    return "\n\n".join(parts)


def run_panel(
    image_path: str,
    item: Dict[str, Any],
    judges: List[Dict[str, Any]],
    router: Dict[str, Any],
    parallel: int = 4,
    threshold: int = 3,
) -> Dict[str, Any]:
    """Jalankan semua juri paralel. Juri yang error abstain dari voting."""
    data_url = encode_image(image_path)

    def one(judge: Dict[str, Any]) -> Dict[str, Any]:
        judge_id = judge.get("id", "sell")
        started = time.time()
        try:
            result = call_model(
                judge["model"], build_prompt(judge_id, item), data_url, router
            )
            return {
                "id": judge_id,
                "label": judge.get("label", judge_id),
                "model": judge["model"],
                "ok": True,
                "score": int(result.get("score", 0)),
                "pass": bool(result.get("pass", False)),
                "reason": str(result.get("reason", ""))[:160],
                "seconds": round(time.time() - started, 1),
            }
        except Exception as e:
            return {
                "id": judge_id,
                "label": judge.get("label", judge_id),
                "model": judge["model"],
                "ok": False,
                "score": 0,
                "pass": False,
                "reason": f"error: {str(e)[:120]}",
                "seconds": round(time.time() - started, 1),
            }

    with ThreadPoolExecutor(max_workers=max(1, min(parallel, len(judges)))) as pool:
        results = list(pool.map(one, judges))

    answered = [r for r in results if r["ok"]]
    votes = sum(1 for r in answered if r["pass"])
    scores = [r["score"] for r in answered]
    # Juri yang error abstain. Kalau tinggal 2 yang menjawab, keduanya harus setuju.
    required = min(threshold, len(answered)) if answered else threshold
    return {
        "judges": results,
        "votes": votes,
        "answered": len(answered),
        "required": required,
        "passed": len(answered) >= 2 and votes >= required,
        "avg_score": round(sum(scores) / len(scores), 1) if scores else 0.0,
    }
