# Panduan Pengembangan StockForge

Dokumen ini untuk siapa pun yang akan melanjutkan proyek ini — termasuk kamu sendiri enam bulan dari sekarang.
Isinya: peta kode, alur data, kontrak API, resep penambahan fitur, dan daftar jebakan yang sudah pernah menggigit.

- Spesifikasi desain & keputusan produk: [`specs/2026-10-29-stockforge-design.md`](specs/2026-10-29-stockforge-design.md)
- Panduan pemakaian untuk pengguna: [`../README.md`](../README.md)

---

## 1. Prinsip yang tidak boleh dilanggar

1. **Generate gambar lewat browser, bukan API.** Akun Gemini kena limit kalau dipanggil lewat API.
   `app/gemini_automator.py` mengendalikan Chrome asli dengan profil yang sudah login.
2. **9router hanya untuk juri (teks/visi), tidak untuk membuat gambar.**
3. **Manusia tetap pemutus terakhir.** Panel hanya memangkas; tidak ada yang terupload otomatis.
4. **Jangan hapus hasil kerja.** Gambar yang ditolak panel dipindah ke `output/_rejected`, bukan dihapus.
   Keputusan user (`approve`/`reject`) dibawa ke run berikutnya lewat `_carry_over_decisions()`.
5. **Kegagalan satu bagian tidak boleh menghentikan seluruh run.** Log tidak boleh melempar error,
   juri yang error abstain, metadata gagal tetap dicatat sebagai peringatan.

## 2. Peta kode

| File | Tanggung jawab | Titik masuk penting |
|---|---|---|
| `app/config.py` | muat/simpan `config.json`, resolve path relatif ke root proyek | `load_config`, `save_config`, `resolve`, `profile_dirs` |
| `app/sheets.py` | ambil CSV Google Sheets (gviz → fallback export), parsing kolom, kelompokkan per konsep | `load_plan`, `parse_csv`, `group_by_concept`, `concept_key` |
| `app/gemini_automator.py` | Playwright + Chrome persistent profile: buka Gemini, kirim prompt, tunggu render, unduh, deteksi limit | `GeminiAutomator.launch/generate_image/switch_profile/check_for_rate_limit` |
| `app/metadata.py` | pembungkus tipis: tanam metadata + verifikasi dengan membaca ulang byte file | `inject`, `verify` |
| `app/metadata_injector.py` | penulis segmen JPEG: XMP, IPTC (8BIM), EXIF — tanpa library tambahan selain Pillow untuk EXIF | `inject_jpeg_metadata` |
| `app/judges.py` | panel 4 juri vision lewat 9router, parser SSE yang tahan banting, voting | `run_panel`, `call_model`, `build_prompt`, `extract_json` |
| `app/ranking.py` | aturan "1–3 terbaik per konsep" | `rank_concept` |
| `app/pipeline.py` | mesin state: orkestrasi generate → metadata → juri → peringkat; kontrol pause/resume/profil | `Pipeline.start`, `_worker`, `_generate_one`, `_judge_concept`, `decide` |
| `app/worklog.py` | log pekerjaan harian (JSONL append-only) | `log`, `read`, `summarize`, `days` |
| `app/server.py` | HTTP server stdlib: API + static + thumbnail cache | `Handler.do_GET`, `Handler.do_POST`, `_thumbnail`, `main` |
| `app/queue_manager.py` | util lama: validasi file gambar | `is_file_valid` (dipakai automator) |
| `app/stock_optimizer.py` | util lama: perluasan keyword + dHash duplikat | **belum dipakai pipeline** — kandidat fitur (lihat §9) |
| `web/` | dashboard satu halaman: tab Gambar & tab Log | `index.html`, `app.js`, `style.css` |
| `tests/` | unit test (unittest, tanpa dependensi tambahan) | `test_sheets`, `test_judges`, `test_ranking`, `test_metadata`, `test_worklog` |

## 3. Alur data

```
Google Sheets  ──► sheets.load_plan()  ──► 90 baris
                        │
                        ▼
              group_by_concept()  ──► 15 konsep × 6 variasi
                        │
        (concept_limit > 0 memotong daftar konsep — untuk uji coba)
                        ▼
   ┌──────────── per konsep (di thread worker) ────────────┐
   │ 1. generate  gemini_automator → output/<Filename>     │
   │ 2. metadata  metadata.inject + verify (XMP/IPTC/EXIF) │
   │ 3. juri      judges.run_panel (4 paralel, ~12-15 s)   │
   │ 4. peringkat ranking.rank_concept → 1-3 terbaik       │
   │    sisanya dipindah ke output/_rejected               │
   └───────────────────────────────────────────────────────┘
                        ▼
        state.json  ──►  /api/state  ──►  dashboard
                        └►  logs/worklog-YYYY-MM-DD.jsonl
```

Urutannya **per konsep**, bukan "generate semua lalu nilai semua". Alasannya: user bisa mulai mengupload
setelah konsep pertama selesai, dan hasil generate 6 variasi masih "segar" di konteks Gemini.

## 4. State dan transisinya

`state.json` disimpan setiap langkah (`Pipeline._save`) dan dimuat ulang saat server start (`_load_state`).

```
pending ──► generating ──► generated ──► judged ──► shortlisted ──► approved
                 │             │                        │
                 └► failed     └► (metadata warn)       └► rejected ──► (regenerate) ──► pending
```

Field per item:

| Field | Isi |
|---|---|
| `filename,title,keywords,category,prompt,concept` | disalin dari spreadsheet, dipakai juri & metadata |
| `status` | salah satu status di atas |
| `error` | pesan terakhir kalau gagal |
| `judgement` | `{judges:[{id,score,pass,reason,seconds}], votes, answered, required, passed, avg_score}` |
| `metadata` | `{ok, xmp, iptc, title, keywords_found, keywords_total}` |
| `decision` | `""`, `pending_review`, `auto_rejected`, `approve`, `reject`, `regenerate` |

Status run (`state.run.status`): `idle`, `starting`, `running`, `paused`, `waiting_profile`,
`waiting_login`, `stopping`, `stopped`, `done`, `error`.
`waiting_profile` dan `waiting_login` adalah keadaan yang **menunggu user** — dashboard menampilkan banner
dan tombol Lanjut; worker berhenti di `_wait_for_profile()` sampai `resume()` dipanggil.

## 5. Konfigurasi (`config.json`)

| Kunci | Default | Efek |
|---|---|---|
| `sheet_url` | — | link spreadsheet terakhir, diisi otomatis di dashboard |
| `sheet_name` | `Master Prompt & Ide Lengkap` | nama tab; kalau gagal, fallback ke ekspor CSV sheet pertama |
| `output_dir` | `output` | hasil generate |
| `shortlist_dir` | `output/_shortlist` | salinan file yang kamu setujui |
| `rejected_dir` | `output/_rejected` | file yang dibuang panel/user |
| `profiles` | 8 entri `profiles/akun-N` | daftar akun Chrome; `dir` boleh path absolut |
| `active_profile` | `0` | indeks profil yang dipakai |
| `chrome_executable` | kosong → auto-detect | path `chrome.exe` |
| `headless` | `false` | **jangan** diaktifkan; Gemini butuh jendela nyata |
| `delay_between_prompts_sec` | `12` | jeda antar prompt |
| `timeout_per_image_sec` | `900` | batas tunggu satu gambar |
| `max_per_concept` | `3` | maksimal gambar lolos per konsep |
| `concept_limit` | `0` | batasi jumlah konsep per run (0 = semua); pakai `1` untuk uji |
| `vote_threshold` | `3` | jumlah juri yang harus setuju |
| `judge_parallel` | `4` | juri dijalankan paralel |
| `port` | `8770` | port dashboard |
| `router.url` | `http://localhost:20128/v1/chat/completions` | endpoint 9router |
| `router.api_key` | — | dikirim sebagai `Authorization: Bearer` |
| `router.timeout_sec` | `100` | **batas waktu dinding** per panggilan juri |
| `judges[]` | 4 entri | `{id, label, model}`; `id` menentukan rubrik |

`config.json` ditulis ulang oleh `save_config()` saat run dimulai dan saat ganti profil.
**Jangan mengedit `config.json` ketika server sedang jalan** — memori server akan menimpanya.

## 6. API

| Metode & rute | Body | Keterangan |
|---|---|---|
| `GET /api/state` | — | snapshot state lengkap |
| `GET /api/config` | — | profil + daftar juri (tanpa API key) |
| `GET /api/log?day=&limit=` | — | `{summary:{day,total,events,days}, entries:[]}` terbaru dulu |
| `GET /api/log/download?day=` | — | unduh JSONL hari itu |
| `GET /api/image?name=&thumb=1` | — | file asli atau thumbnail 480px (cache di `output/_thumbs`) |
| `POST /api/start` | `{sheet_url}` | mulai run (tolak kalau masih ada worker hidup) |
| `POST /api/pause` | `{}` | jeda setelah langkah saat ini |
| `POST /api/resume` | `{}` | lanjut (juga dipakai untuk "Lanjut" setelah ganti akun) |
| `POST /api/stop` | `{}` | hentikan |
| `POST /api/profile` | `{index}` | ganti profil Chrome, lalu lanjut |
| `POST /api/decide` | `{filename, action}` | `approve` \| `reject` \| `regenerate` |
| `POST /api/settings` | `{max_per_concept?...}` | ubah sebagian config |

Semua respons JSON. Kesalahan rute → `{"error": "rute tidak ada"}` dengan kode 404.

## 7. Menjalankan & mendebug

```bash
# sekali saja
py -3.12 -m venv .venv
.venv/Scripts/python.exe -m pip install playwright pillow

# jalankan
./start.bat          # atau: .venv/Scripts/python.exe app/server.py

# test
.venv/Scripts/python.exe -m unittest discover -s tests -t . -v

# tanpa membuka browser otomatis
STOCKFORGE_NO_BROWSER=1 .venv/Scripts/python.exe app/server.py
```

Debug cepat:

- **Uji 1 konsep saja** → set `"concept_limit": 1` di `config.json`, lalu Mulai.
- **Lihat apa yang sebenarnya terjadi** → tab Log, atau `tail` file `logs/worklog-YYYY-MM-DD.jsonl`.
- **Uji satu juri tanpa pipeline**:

```python
import sys; sys.path.insert(0, ".")
from app import config, judges
cfg = config.load_config()
print(judges.call_model("ag/gemini-3.8-flash", judges.build_prompt("event", {"concept": "Ide 66: Earth Hour 2027"}),
                        judges.encode_image("output/contoh-hasil.jpg"), cfg["router"]))
```

- **Cek port bentrok**: `netstat -ano | grep 8770` (Windows menyembunyikan double-bind; server sekarang
  menolak start kalau port terpakai).
- **Profil Chrome terkunci**: tutup semua Chrome; hapus `SingletonLock`/`SingletonCookie` di folder profil.

## 8. Resep pengembangan

### Menambah juri baru

1. Tambah rubrik di `judges.RUBRICS` dengan kunci `id` baru.
2. Tambah entri di `config.json` → `judges[]`: `{"id": "...", "label": "...", "model": "..."}`.
3. Kalau juri butuh konteks tambahan (judul/keyword/konsep), tambahkan di `judges.build_prompt()`.
4. Sesuaikan `vote_threshold` kalau jumlah juri berubah (misal 5 juri → ambang 4).

Catatan: `run_panel` sudah menangani juri yang error (abstain) dan ambang dinamis
(`required = min(threshold, answered)`, minimal 2 juri harus menjawab).

### Mengganti model juri

Uji dulu apakah model itu benar-benar bisa melihat gambar dan mengembalikan JSON:

```bash
.venv/Scripts/python.exe - <<'PY'
import sys; sys.path.insert(0, ".")
from app import config, judges
cfg = config.load_config()
img = judges.encode_image("output/contoh-hasil.jpg")
for m in ["ag/gemini-3.8-flash", "1/glm-5.3-flash", "1/deepseek-v4.1-flash", "ag/claude-sonnet-5-5-medium"]:
    try:
        r = judges.call_model(m, 'Balas hanya JSON: {"score":<0-100>,"pass":<bool>,"reason":"<=10 kata"}', img, cfg["router"])
        print(m, "OK", r)
    except Exception as e:
        print(m, "GAGAL", e)
PY
```

### Mengubah aturan peringkat

Semua di `ranking.rank_concept()`: urutan `(-votes, -avg_score)`, potong `max_per_concept`, fallback
1 terbaik + `low_confidence` kalau tidak ada yang lolos. Jangan menaruh aturan lain di `pipeline.py`.

### Menambah langkah baru ke pipeline

Tempat yang benar: `Pipeline._generate_one()` (setelah gambar turun) atau `_judge_concept()` (sebelum/sesudah
panel). Pola yang dipakai:

```python
with self.lock:              # ubah state
    item["status"] = "..."
    self._recount(); self._save()
worklog.log("event_baru", "pesan untuk manusia", filename=item["filename"])
```

Lalu daftarkan label + kategori event itu di `web/app.js` (`LOG_LABELS`, `LOG_CATEGORY`) supaya tampil rapi.

### Menambah endpoint + UI

1. Tambah cabang di `server.Handler.do_GET` / `do_POST` (pola: baca query → panggil → `self._json(...)`).
2. Tambah elemen di `web/index.html`, gaya di `web/style.css`, logika di `web/app.js`.
   `poll()` berjalan tiap 2,5 detik — taruh pembaruan UI di sana kalau datanya berubah sendiri.

### Ide pengembangan yang sudah terpetakan

| Ide | Di mana mulai | Catatan |
|---|---|---|
| Kalibrasi panel dengan data download Adobe | modul baru `app/calibration.py` + kolom di state | butuh ekspor CSV dari dashboard kontributor; bandingkan `decision` vs download |
| Regenerate dengan diagnosis juri | `_generate_one` — sisipkan alasan juri ke prompt | data sudah ada di `item["judgement"]["judges"][].reason` |
| Juri kepadatan pasar | juri baru + buka halaman pencarian Adobe di tab lain | rubrik baru, bukan model baru |
| Dua worker paralel | pecah `Pipeline` per profil, state per worker | hati-hati: `automator` tidak thread-safe |
| Penjaga duplikat lintas portofolio | `stock_optimizer.compute_dhash` (sudah ada, belum dipakai) | tolak yang mirip >98% sebelum masuk panel |
| Pengingat tenggat musiman | `sheets.event_label` sudah menangkap tanggal event | hitung H-N, tandai terlambat di kartu |
| Peta `Category` Adobe (ID → nama) | `web/app.js` bagian kartu | ID kategori dari spreadsheet berupa angka |

## 9. Jebakan yang sudah ditemukan (jangan diulang)

| Gejala | Penyebab | Penanganan yang sudah ada |
|---|---|---|
| Respons juri kosong padahal modelnya hidup | 9router mengirim SSE, kadang berupa satu JSON dengan `data: [DONE]` menempel di ekor | `judges._iter_json_objects` + `_collect_text` |
| `Missing API key` | header `Authorization` lupa dikirim | `call_model` mengirim Bearer kalau `router.api_key` terisi |
| Juri menggantung lama sekali | koneksi streaming ditahan terbuka; timeout socket tidak cukup | `call_model` memakai **batas waktu dinding** (`router.timeout_sec`) saat membaca stream |
| Model menjawab "tidak ada foto yang dikirim" | model menerima blok gambar tapi tidak memprosesnya (contoh: `ag/claude-*`) | ganti model; sudah diuji mana yang jalan |
| `1/gpt-*`, `1/kimi-*` gagal | provider itu tidak melayani input gambar | pakai `ag/gemini-*`, `1/glm-*`, `1/deepseek-*` |
| Dua server di port yang sama | Windows mengizinkan double-bind dengan `SO_REUSEADDR` | `allow_reuse_address = False` + pesan error jelas |
| Perubahan UI tidak muncul | header cache `max-age` untuk aset web | aset `web/` sekarang dikirim `no-cache`; kalau masih, Ctrl+Shift+R |
| Config ter-reset sendiri | server lama (yang masih hidup) menulis ulang config-nya | pastikan hanya satu server jalan; cek `netstat` |
| Generate ulang file yang sudah diputuskan | state dibangun ulang tiap run dari spreadsheet | `_carry_over_decisions()` mencocokkan `filename` |
| Gambar tidak bisa di-fetch sebagai blob URL | Gemini memakai blob + RPC `batchexecute` | `generate_image` memakai tombol download resmi, lalu fallback canvas |
| XMP hilang setelah injeksi | segmen lama tidak dibuang sebelum menulis yang baru | `inject_jpeg_metadata` memfilter APP1/APP13 lama dulu |
| Test mengotori log kerja | test menulis ke `logs/` asli | `tests/test_worklog.py` mengalihkan `worklog.LOG_DIR` ke folder sementara |

## 10. Batasan yang masih ada

- Satu worker, satu profil Chrome pada satu waktu. 90 gambar ≈ 3–5 jam.
- Ganti akun masih manual (sesuai keputusan user); tidak ada deteksi limit otomatis lintas profil.
- `Category` Adobe belum dipetakan ke nama; masih ditampilkan sebagai angka.
- Belum ada pembersihan otomatis `output/` dan `logs/`; user menghapus manual kalau perlu.
- Juri menilai gambar satu per satu; tidak ada penilaian komparatif antar variasi.
- Tidak ada autentikasi di dashboard — server hanya bind ke `127.0.0.1`.

## 11. Konvensi kode

- Komentar dan pesan untuk pengguna dalam **Bahasa Indonesia**; nama fungsi/variabel Bahasa Inggris.
- Setiap aksi yang terlihat oleh user **wajib** dicatat lewat `worklog.log(event, pesan, **field)`.
- `worklog.log` tidak boleh melempar error (sudah dibungkus try/except) — jangan menambah I/O yang bisa gagal
  di jalur kritis tanpa membungkusnya.
- Path selalu lewat `config.resolve()`; jangan `os.getcwd()`.
- Fungsi baru yang murni logika (parsing, perankingan, metadata) harus punya test di `tests/`.
- UI: jangan menambah `max-age` untuk aset `web/`.

## 12. Checklist sebelum menyerahkan perubahan

1. `python -m unittest discover -s tests -t . -v` → semua lolos.
2. Jalankan dengan `concept_limit: 1` dan pastikan alur penuh sampai `run_end` (6 gambar, 1–3 lolos).
3. Buka tab Log: pastikan kejadian baru muncul dengan label yang terbaca.
4. Cek `state.json` tidak tumbuh liar dan `output/_thumbs` tidak menumpuk file yatim.
5. Kalau menyentuh `server.py`: pastikan hanya satu proses bisa bind ke port.
