"""Peringkat per konsep: dari 6 variasi ambil 1-3 terbaik."""

from typing import Any, Dict, List


def rank_concept(
    variations: List[Dict[str, Any]],
    max_per_concept: int = 3,
    threshold: int = 3,
) -> Dict[str, Any]:
    """variations: list dict berisi 'filename' dan 'judgement' (hasil run_panel).

    Aturan:
    - Yang voting-nya >= threshold dianggap lolos, diurutkan skor rata-rata.
    - Kalau tidak ada yang lolos sama sekali, ambil satu terbaik sebagai "low confidence"
      supaya user tetap punya bahan, bukan konsep kosong.
    - Maksimal max_per_concept gambar per konsep.
    """
    scored: List[Dict[str, Any]] = []
    for v in variations:
        j = v.get("judgement") or {}
        scored.append(
            {
                "filename": v["filename"],
                "votes": j.get("votes", 0),
                "avg_score": j.get("avg_score", 0.0),
                "passed": bool(j.get("passed")),
            }
        )

    passed = [s for s in scored if s["passed"]]
    passed.sort(key=lambda s: (-s["votes"], -s["avg_score"]))
    shortlist = passed[:max_per_concept]

    low_confidence = False
    if not shortlist:
        ranked = sorted(scored, key=lambda s: (-s["votes"], -s["avg_score"]))
        if ranked:
            shortlist = ranked[:1]
            low_confidence = True

    chosen = {s["filename"] for s in shortlist}
    rejected = [s["filename"] for s in scored if s["filename"] not in chosen]

    return {
        "shortlist": shortlist,
        "rejected": rejected,
        "low_confidence": low_confidence,
    }
