"""Mesin pipeline: generate -> metadata -> panel juri -> peringkat per konsep."""

import json
import os
import shutil
import threading
import time
from typing import Any, Dict, List, Optional

from app import config as cfgmod
from app import judges as judgemod
from app import ranking
from app import sheets
from app import worklog
from app.metadata import inject as inject_metadata, verify as verify_metadata


class Pipeline:
    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg
        self.lock = threading.RLock()
        self.thread: Optional[threading.Thread] = None
        self.automator = None
        self._resume = threading.Event()
        self._stop = threading.Event()
        self._paused = False
        self._pending_profile: Optional[int] = None
        self._previous_items: Dict[str, Any] = {}
        self.state: Dict[str, Any] = self._blank_state()
        self._load_state()

    def _load_state(self) -> None:
        """Pulihkan state terakhir supaya dashboard tidak kosong setelah server restart."""
        try:
            if not os.path.exists(cfgmod.STATE_PATH):
                return
            with open(cfgmod.STATE_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
            if isinstance(saved, dict) and saved.get("concepts"):
                self.state = saved
                if self.state["run"]["status"] in ("running", "starting"):
                    self.state["run"]["status"] = "stopped"
                    self.state["run"]["message"] = "Server dijalankan ulang. Tekan Mulai untuk melanjutkan."
        except Exception as e:
            print(f"[STATE] Gagal memuat state lama: {e}")

    # ---------- state ----------
    def _blank_state(self) -> Dict[str, Any]:
        return {
            "run": {
                "status": "idle",
                "message": "Siap. Tempel link spreadsheet lalu tekan Mulai.",
                "current": "",
                "sheet_url": self.cfg.get("sheet_url", ""),
                "started_at": None,
                "finished_at": None,
                "profile": self._profile_name(self.cfg.get("active_profile", 0)),
            },
            "concepts": [],
            "progress": {"total": 0, "generated": 0, "judged": 0, "shortlisted": 0, "rejected": 0},
        }

    def _profile_name(self, idx: int) -> str:
        profiles = self.cfg.get("profiles", [])
        if 0 <= idx < len(profiles):
            return profiles[idx].get("name") or f"Akun {idx + 1}"
        return "-"

    def snapshot(self) -> Dict[str, Any]:
        with self.lock:
            return json.loads(json.dumps(self.state))

    def _save(self) -> None:
        try:
            with open(cfgmod.STATE_PATH, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=1, ensure_ascii=False)
        except Exception as e:
            print(f"[STATE] Gagal menyimpan state: {e}")

    def _set_run(self, status: str, message: str = "", current: str = "") -> None:
        with self.lock:
            self.state["run"]["status"] = status
            if message:
                self.state["run"]["message"] = message
            if current:
                self.state["run"]["current"] = current
            self._save()

    def _recount(self) -> None:
        items = [i for c in self.state["concepts"] for i in c["items"]]
        with self.lock:
            self.state["progress"] = {
                "total": len(items),
                "generated": sum(1 for i in items if i["status"] in ("generated", "judged", "shortlisted", "rejected")),
                "judged": sum(1 for i in items if i.get("judgement")),
                "shortlisted": sum(1 for i in items if i["status"] == "shortlisted"),
                "rejected": sum(1 for i in items if i["status"] == "rejected"),
            }

    # ---------- carry over ----------
    def _carry_over_decisions(self) -> None:
        """Bawa status/keputusan dari run sebelumnya untuk filename yang sama."""
        prev = getattr(self, "_previous_items", {}) or {}
        if not prev:
            return
        shortlist = cfgmod.resolve(self.cfg["shortlist_dir"])
        rejected = cfgmod.resolve(self.cfg["rejected_dir"])
        out_dir = cfgmod.resolve(self.cfg["output_dir"])
        with self.lock:
            for concept in self.state["concepts"]:
                for item in concept["items"]:
                    old = prev.get(item["filename"])
                    if not old:
                        continue
                    name = item["filename"]
                    exists = any(os.path.exists(os.path.join(d, name)) for d in (out_dir, shortlist, rejected))
                    if not exists:
                        continue
                    item["judgement"] = old.get("judgement")
                    item["metadata"] = old.get("metadata")
                    item["decision"] = old.get("decision", "")
                    item["status"] = old.get("status", "pending")

    # ---------- kontrol dari dashboard ----------
    def start(self, sheet_url: str) -> Dict[str, Any]:
        if self.thread and self.thread.is_alive():
            return {"ok": False, "error": "Proses masih berjalan."}
        if not (sheet_url or "").strip():
            return {"ok": False, "error": "Link spreadsheet kosong."}

        # Simpan keputusan run sebelumnya supaya file yang sudah disetujui/dibuang
        # tidak digenerate ulang saat sheet yang sama dijalankan lagi.
        self._previous_items = {}
        for concept in self.state.get("concepts", []):
            for item in concept.get("items", []):
                if item.get("decision") or item.get("judgement"):
                    self._previous_items[item["filename"]] = item

        with self.lock:
            self.state = self._blank_state()
            self.state["run"]["sheet_url"] = sheet_url.strip()
            self.state["run"]["status"] = "starting"
            self.state["run"]["message"] = "Mengambil data spreadsheet..."
        worklog.log("run_start", "Run dimulai", sheet_url=sheet_url.strip()[:120])
        self._stop.clear()
        self._resume.set()
        self._paused = False

        self.cfg["sheet_url"] = sheet_url.strip()
        cfgmod.save_config(self.cfg)

        self.thread = threading.Thread(target=self._worker, daemon=True)
        self.thread.start()
        return {"ok": True}

    def pause(self) -> Dict[str, Any]:
        self._paused = True
        self._resume.clear()
        worklog.log("pause", "Run dijeda")
        self._set_run("paused", "Dijeda oleh kamu.")
        return {"ok": True}

    def resume(self) -> Dict[str, Any]:
        self._paused = False
        self._resume.set()
        worklog.log("resume_run", "Run dilanjutkan")
        if self.state["run"]["status"] in ("paused", "waiting_profile"):
            self._set_run("running", "Melanjutkan...")
        return {"ok": True}

    def stop(self) -> Dict[str, Any]:
        self._stop.set()
        self._resume.set()
        worklog.log("stop", "User menghentikan run")
        self._set_run("stopping", "Menghentikan setelah langkah ini...")
        return {"ok": True}

    def switch_profile(self, index: int) -> Dict[str, Any]:
        profiles = self.cfg.get("profiles", [])
        if not (0 <= index < len(profiles)):
            return {"ok": False, "error": "Profil tidak ada."}
        self._pending_profile = index
        if self.state["run"]["status"] == "waiting_profile":
            self._set_run("waiting_profile", f"Dilanjutkan dengan {self._profile_name(index)}...")
            self._resume.set()
        return {"ok": True, "profile": self._profile_name(index)}

    def decide(self, filename: str, action: str) -> Dict[str, Any]:
        """approve / reject / regenerate untuk satu file di dashboard."""
        with self.lock:
            for concept in self.state["concepts"]:
                for item in concept["items"]:
                    if item["filename"] != filename:
                        continue
                    path = os.path.join(cfgmod.resolve(self.cfg["output_dir"]), filename)
                    shortlist = cfgmod.resolve(self.cfg["shortlist_dir"])
                    rejected = cfgmod.resolve(self.cfg["rejected_dir"])
                    if action == "approve":
                        os.makedirs(shortlist, exist_ok=True)
                        if os.path.exists(path):
                            shutil.copy2(path, os.path.join(shortlist, filename))
                        item["status"] = "approved"
                        item["decision"] = "approve"
                    elif action == "reject":
                        os.makedirs(rejected, exist_ok=True)
                        if os.path.exists(path):
                            shutil.move(path, os.path.join(rejected, filename))
                        item["status"] = "rejected"
                        item["decision"] = "reject"
                    elif action == "regenerate":
                        item["status"] = "pending"
                        item["decision"] = "regenerate"
                        item["judgement"] = None
                    else:
                        return {"ok": False, "error": "Aksi tidak dikenal."}
                    label = {"approve": "disetujui", "reject": "dibuang", "regenerate": "dijadwalkan ulang"}[action]
                    worklog.log(f"user_{action}", f"{filename} {label}", filename=filename)
                    self._recount()
                    self._save()
                    return {"ok": True}
        return {"ok": False, "error": "File tidak ditemukan di rencana."}

    # ---------- worker ----------
    def _wait_if_needed(self) -> bool:
        """True kalau boleh lanjut, False kalau harus berhenti."""
        if self._stop.is_set():
            return False
        if not self._resume.is_set():
            self._resume.wait(timeout=1)
            if not self._resume.is_set():
                return self._wait_if_needed()
        return not self._stop.is_set()

    def _wait_for_profile(self, reason: str) -> bool:
        worklog.log("waiting_user", reason)
        self._set_run("waiting_profile", reason)
        while not self._stop.is_set():
            if self._resume.wait(timeout=1) and self._resume.is_set() and self.state["run"]["status"] != "waiting_profile":
                return True
        return False

    def _apply_pending_profile(self) -> None:
        if self._pending_profile is None:
            return
        idx = self._pending_profile
        self._pending_profile = None
        self.cfg["active_profile"] = idx
        cfgmod.save_config(self.cfg)
        if self.automator is not None:
            profiles = cfgmod.profile_dirs(self.cfg)
            target = profiles[idx]["dir"]
            self.automator.switch_profile(target)
            self.automator.navigate_to_gemini()
            worklog.log("profile_switch", f"Pindah ke {profiles[idx]['name']}", profile=profiles[idx]["name"])
        with self.lock:
            self.state["run"]["profile"] = self._profile_name(idx)

    def _worker(self) -> None:
        try:
            self._set_run("running", "Mengambil data spreadsheet...")
            plan = sheets.load_plan(self.cfg["sheet_url"], self.cfg.get("sheet_name", ""))
            if not plan:
                self._set_run("error", "Spreadsheet tidak berisi baris dengan Filename + Prompt.")
                return

            concepts = sheets.group_by_concept(plan)
            limit = int(self.cfg.get("concept_limit", 0) or 0)
            if limit > 0:
                concepts = concepts[:limit]
            with self.lock:
                self.state["concepts"] = [
                    {
                        "key": c["key"],
                        "label": c["label"],
                        "items": [
                            {
                                **{k: it.get(k, "") for k in ("filename", "title", "keywords", "category", "prompt", "concept")},
                                "status": "pending",
                                "error": "",
                                "judgement": None,
                                "metadata": None,
                                "decision": "",
                            }
                            for it in c["items"]
                        ],
                    }
                    for c in concepts
                ]
                self.state["run"]["started_at"] = time.time()
            self._carry_over_decisions()
            carried = sum(1 for c in self.state["concepts"] for i in c["items"] if i.get("decision"))
            if carried:
                worklog.log("resume", f"{carried} gambar dilewati karena sudah diputuskan")
            worklog.log(
                "sheet_loaded",
                f"{len(plan)} baris, {len(concepts)} konsep",
                items=len(plan),
                concepts=len(concepts),
            )
            self._recount()
            self._save()
            self._set_run("running", f"{len(plan)} gambar dalam {len(concepts)} konsep. Membuka Chrome...")

            if not self._launch_browser():
                return

            for concept in self.state["concepts"]:
                if self._stop.is_set():
                    break
                self._process_concept(concept)

            self._finalize()

        except Exception as e:
            import traceback
            traceback.print_exc()
            worklog.log("error", f"Run berhenti karena error: {str(e)[:160]}")
            self._set_run("error", f"Berhenti karena error: {e}")
        finally:
            if self.automator is not None:
                try:
                    self.automator.close()
                except Exception:
                    pass
                self.automator = None
            with self.lock:
                if self.state["run"]["status"] not in ("error",):
                    self.state["run"]["finished_at"] = time.time()
                self._recount()
                self._save()

    def _launch_browser(self) -> bool:
        from app.gemini_automator import GeminiAutomator

        profiles = cfgmod.profile_dirs(self.cfg)
        if not profiles:
            self._set_run("error", "Belum ada profil Chrome di config.")
            return False
        idx = min(self.cfg.get("active_profile", 0), len(profiles) - 1)
        self.automator = GeminiAutomator(
            user_data_dir=profiles[idx]["dir"],
            chrome_executable=self.cfg.get("chrome_executable") or None,
            headless=bool(self.cfg.get("headless")),
            watchdog_timeout_sec=int(self.cfg.get("timeout_per_image_sec", 900)),
        )
        self.automator.launch()
        self.automator.navigate_to_gemini()
        worklog.log("browser_open", f"Chrome dibuka dengan {profiles[idx]['name']}", profile=profiles[idx]["name"])
        if not self.automator.is_logged_in():
            worklog.log("login_needed", f"Belum login di {profiles[idx]['name']}")
            self._set_run(
                "waiting_login",
                f"Belum login di {profiles[idx]['name']}. Login di jendela Chrome, lalu tekan Lanjut.",
            )
            if not self._wait_for_profile("Menunggu login Google di jendela Chrome..."):
                return False
            self.automator.navigate_to_gemini()
        return True

    def _process_concept(self, concept: Dict[str, Any]) -> None:
        with self.lock:
            self.state["run"]["current"] = concept["label"]

        pending = [i for i in concept["items"] if i["status"] == "pending"]
        if not pending:
            return

        for item in pending:
            if not self._wait_if_needed():
                return
            self._apply_pending_profile()
            self._generate_one(concept, item)

        self._judge_concept(concept)

    def _generate_one(self, concept: Dict[str, Any], item: Dict[str, Any]) -> None:
        out_dir = cfgmod.resolve(self.cfg["output_dir"])
        target = os.path.join(out_dir, item["filename"])
        self._set_run("running", f"Generate: {item['filename']}", current=f"{concept['label']} / {item['filename']}")

        with self.lock:
            item["status"] = "generating"
            self._save()

        if os.path.exists(target) and os.path.getsize(target) > 10_240:
            with self.lock:
                item["status"] = "generated"
                item["error"] = ""
            print(f"[SKIP] {item['filename']} sudah ada.")
            return

        result = {"status": "error", "message": "automator tidak siap"}
        for attempt in range(1, 3):
            result = self.automator.generate_image(
                item["prompt"],
                target,
                timeout=int(self.cfg.get("timeout_per_image_sec", 900)),
            )
            if result.get("status") == "success":
                break
            if result.get("status") == "rate_limit":
                worklog.log(
                    "rate_limit",
                    f"Akun {self.state['run']['profile']} kena limit di {item['filename']}",
                    filename=item["filename"],
                )
                if not self._wait_for_profile(
                    f"Akun {self.state['run']['profile']} kena limit. Ganti akun di Chrome lalu tekan Lanjut, "
                    "atau pilih profil lain di dashboard."
                ):
                    return
                self._apply_pending_profile()
            elif result.get("status") == "login_required":
                if not self._wait_for_profile("Sesi login habis. Login ulang di Chrome lalu tekan Lanjut."):
                    return
            else:
                time.sleep(3)

        with self.lock:
            if result.get("status") == "success":
                item["status"] = "generated"
                item["error"] = ""
            else:
                item["status"] = "failed"
                item["error"] = str(result.get("message", ""))[:200]

        if item["status"] == "generated":
            # Tanam metadata segera setelah file turun, supaya file di disk sudah lengkap
            # walaupun nanti panel juri gagal.
            try:
                inject_metadata(target, item)
            except Exception as e:
                item["error"] = f"inject gagal: {e}"[:160]
            item["metadata"] = verify_metadata(target, item)
            size = os.path.getsize(target) if os.path.exists(target) else 0
            worklog.log(
                "generate_ok",
                f"{item['filename']} selesai ({round(size / 1e6, 2)} MB)",
                filename=item["filename"],
                mb=round(size / 1e6, 2),
            )
            meta = item["metadata"] or {}
            if meta.get("ok"):
                worklog.log(
                    "metadata_ok",
                    f"Metadata tertanam: {meta.get('keywords_found')}/{meta.get('keywords_total')} keyword",
                    filename=item["filename"],
                    keywords=meta.get("keywords_found"),
                )
            else:
                worklog.log(
                    "metadata_warn",
                    f"Metadata belum lengkap di {item['filename']}",
                    filename=item["filename"],
                    keywords=meta.get("keywords_found"),
                )
        else:
            worklog.log(
                "generate_fail",
                f"{item['filename']} gagal: {str(item.get('error') or 'tanpa pesan')[:120]}",
                filename=item["filename"],
            )

        with self.lock:
            self._recount()
            self._save()

        delay = int(self.cfg.get("delay_between_prompts_sec", 12))
        if delay:
            time.sleep(delay)

    def _judge_concept(self, concept: Dict[str, Any]) -> None:
        generated = [i for i in concept["items"] if i["status"] == "generated"]
        if not generated:
            return

        out_dir = cfgmod.resolve(self.cfg["output_dir"])
        for item in generated:
            if not self._wait_if_needed():
                return
            path = os.path.join(out_dir, item["filename"])
            self._set_run(
                "running",
                f"Panel juri: {item['filename']}",
                current=f"{concept['label']} / {item['filename']}",
            )
            try:
                if not (item.get("metadata") or {}).get("ok"):
                    inject_metadata(path, item)
                    item["metadata"] = verify_metadata(path, item)
                judgement = judgemod.run_panel(
                    path,
                    item,
                    self.cfg["judges"],
                    self.cfg["router"],
                    parallel=int(self.cfg.get("judge_parallel", 4)),
                    threshold=int(self.cfg.get("vote_threshold", 3)),
                )
            except Exception as e:
                judgement = {"judges": [], "votes": 0, "answered": 0, "passed": False,
                             "avg_score": 0.0, "error": str(e)[:200]}
            with self.lock:
                item["judgement"] = judgement
                item["status"] = "judged"
                self._recount()
                self._save()
            reasons = "; ".join(
                f"{j['id']} {j['score']}" for j in (judgement.get("judges") or []) if j.get("ok")
            )
            worklog.log(
                "judged",
                f"{item['filename']}: {judgement.get('votes')}/{judgement.get('answered')} setuju"
                f" (rata-rata {judgement.get('avg_score')})",
                filename=item["filename"],
                votes=judgement.get("votes"),
                passed=judgement.get("passed"),
                scores=reasons[:180],
            )

        verdict = ranking.rank_concept(
            [{"filename": i["filename"], "judgement": i.get("judgement")} for i in generated],
            max_per_concept=int(self.cfg.get("max_per_concept", 3)),
            threshold=int(self.cfg.get("vote_threshold", 3)),
        )
        chosen = {s["filename"] for s in verdict["shortlist"]}
        rejected_dir = cfgmod.resolve(self.cfg["rejected_dir"])
        os.makedirs(rejected_dir, exist_ok=True)

        with self.lock:
            for item in generated:
                path = os.path.join(out_dir, item["filename"])
                if item["filename"] in chosen:
                    item["status"] = "shortlisted"
                    item["decision"] = "pending_review"
                else:
                    item["status"] = "rejected"
                    item["decision"] = "auto_rejected"
                    try:
                        if os.path.exists(path):
                            shutil.move(path, os.path.join(rejected_dir, item["filename"]))
                    except Exception as e:
                        item["error"] = f"gagal pindah: {e}"[:120]
            concept["low_confidence"] = verdict["low_confidence"]
            self._recount()
            self._save()
        worklog.log(
            "concept_ranked",
            f"{concept['label'][:60]}: {len(verdict['shortlist'])} lolos, {len(verdict['rejected'])} dibuang"
            + (" (low confidence)" if verdict["low_confidence"] else ""),
            shortlist=", ".join(s["filename"] for s in verdict["shortlist"])[:200],
        )

    def _finalize(self) -> None:
        status = "stopped" if self._stop.is_set() else "done"
        counts = self.state["progress"]
        started = self.state["run"].get("started_at") or time.time()
        minutes = round((time.time() - started) / 60, 1)
        self._set_run(
            status,
            f"Selesai. {counts['shortlisted']} gambar siap direview, {counts['rejected']} dibuang panel.",
            current="",
        )
        worklog.log(
            "run_end",
            f"{status.upper()} dalam {minutes} menit: {counts['generated']} generate, "
            f"{counts['shortlisted']} lolos, {counts['rejected']} dibuang",
            generated=counts["generated"],
            shortlisted=counts["shortlisted"],
            rejected=counts["rejected"],
            minutes=minutes,
        )
