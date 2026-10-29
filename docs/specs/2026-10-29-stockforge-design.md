# StockForge — Desain Sistem

Tanggal: 2026-10-29
Status: dibangun dan diverifikasi dengan run nyata (bukan mock)

## Niat

Menghapus kerja tangan berulang pada alur harian Adobe Stock: generate gambar di Gemini Web,
menamai file, dan menempelkan judul + 30 keyword. Lalu menambahkan satu hal yang sebelumnya
manual sepenuhnya: menyaring 90 gambar sehari menjadi belasan yang layak diupload.

## Masalah yang diselesaikan

Dari hasil wawancara:

- Waktu paling banyak habis di **generate gambar** dan **menempel metadata**, bukan di memilih ide.
- Volume ~**100 gambar/hari**, tapi yang diupload hanya **10–25**. Artinya 75–90% waktu terbuang.
- Tema mengikuti **event yang akan datang** (contoh: Earth Hour 27 Maret 2027, digenerate 29 Oktober 2026 — sekitar 5 bulan lebih awal).
- Porto tidak bertema tunggal, jadi penilai tidak boleh menyamakan gaya dengan satu tema.
- Spreadsheet harian: 90 baris = **15 konsep × 6 variasi**, kolom `Filename, Title, Keywords, Category, Releases, Ide Konsep, AI Prompt (Siap Pakai)`.
- Punya **8 akun Google**; ingin ganti akun **manual** saat kena limit, lalu sistem melanjutkan.
- Generate **tidak boleh lewat API** (rawan limit) → harus browser yang sudah login.

## Tujuan

1. Satu klik: ambil spreadsheet → generate 6 variasi per konsep di Gemini Web → beri nama sesuai `Filename`.
2. Judul + keyword tertanam di file sehingga form upload Adobe terisi otomatis.
3. Panel juri menyaring per konsep: **maksimal 3 dari 6** yang masuk ke user.
4. Dashboard untuk menyetujui / membuang / mengulang, dan untuk meneruskan setelah ganti akun.
5. Bisa dilanjutkan (resume) tanpa menggenerate ulang file yang sudah ada.

## Bukan tujuan

- Upload otomatis ke Adobe Stock (tetap manual, sesuai permintaan).
- Memilih tema sendiri (tema sudah ditentukan agent di spreadsheet).
- Mengelola langganan/limit akun Google.
- Aplikasi desktop berbasis Electron.

## Keputusan yang diambil user

- Folder baru, terpisah dari `Gemini_Auto_Image_Gen` lama.
- Generate lewat browser, **bukan** API 9router.
- Ganti akun saat limit: **manual** oleh user, sistem menunggu lalu lanjut.
- Setuju konsep peringkat per konsep: **1–3 terbaik per konsep**.

## Keputusan yang diambil builder

| Keputusan | Alasan |
|---|---|
| Python 3.12 + venv lokal | Playwright tersedia untuk 3.12; 3.14 belum didukung penuh |
| Playwright persistent context ke folder profil Chrome | sesi login Google tersimpan; tidak perlu unduh browser lain |
| Port 8770, server stdlib `ThreadingHTTPServer` | tanpa Flask/Node, satu dependensi lebih sedikit |
| Inject XMP + IPTC + EXIF langsung ke JPEG | Adobe Stock membaca metadata terbenam; tidak perlu CSV terpisah |
| Panel 4 juri paralel, satu rubrik per juri | juri yang menilai hal sama 4x hanya mengulang bias yang sama |
| `passed` = votes ≥ min(threshold, jumlah juri yang menjawab), minimal 2 menjawab | juri yang error abstain, tapi 1 suara tidak boleh meloloskan gambar |
| Konsep tanpa satu pun lolos → ambil 1 terbaik, tandai `low_confidence` | supaya tiap event tetap terwakili, dan user tahu itu keputusan tipis |
| Keputusan run sebelumnya dibawa ke run berikutnya | mencegah generate ulang file yang sudah disetujui/dibuang |
| Thumbnail cache 480px | dashboard tidak menarik file 2–9 MB per kartu |

## Arsitektur

```
Spreadsheet (gviz/CSV) → app/sheets.py → kelompok per "Ide N"
        ↓
app/pipeline.py (satu worker thread, state.json)
   1. generate  → app/gemini_automator.py (Playwright + Chrome profil)
   2. metadata  → app/metadata.py (XMP/IPTC/EXIF + verifikasi baca ulang)
   3. nilai     → app/judges.py (4 juri vision via 9router, paralel)
   4. peringkat → app/ranking.py (maks 3 per konsep, sisanya ke _rejected)
        ↓
app/server.py (HTTP API + static) → web/ (dashboard satu halaman)
```

### Model juri (diuji langsung ke 9router)

| Juri | Model | Hasil uji |
|---|---|---|
| Event legibility | `ag/gemini-3.8-flash` | ~9–12s, JSON bersih |
| Kelayakan jual | `1/glm-5.3-flash` | ~9–11s, JSON dibungkus string |
| Teknis & compliance | `1/deepseek-v4.1-flash` | ~10–15s |
| Kecocokan metadata | `ag/gemini-3.8-flash-low` | ~10s |

Yang tidak dipakai: `1/gpt-*` dan `1/kimi-*` (tidak melayani gambar lewat router ini),
`ag/claude-sonnet-5-5-medium` (menerima gambar tapi menjawab "tidak ada foto yang dikirim").

### Bentuk state

`state.json` menyimpan per filename: `status, error, judgement{judges[], votes, passed, avg_score},
metadata{ok, xmp, iptc, keywords_found}, decision`. Status: `pending → generating → generated →
judged → shortlisted / rejected / approved / failed`.

### API

| Rute | Fungsi |
|---|---|
| `GET /api/state` | snapshot lengkap untuk dashboard |
| `GET /api/config` | profil + daftar juri (tanpa API key) |
| `POST /api/start {sheet_url}` | mulai run |
| `POST /api/pause` / `resume` / `stop` | kendali run |
| `POST /api/profile {index}` | pindah profil Chrome lalu lanjut |
| `POST /api/decide {filename, action}` | approve / reject / regenerate |
| `GET /api/log?day=&limit=` | ringkasan + entri log pekerjaan (terbaru dulu) |
| `GET /api/log/download?day=` | unduh log hari itu sebagai `.jsonl` |
| `GET /api/image?name=&thumb=1` | gambar penuh / thumbnail cache |

## Bukti verifikasi (run nyata)

- Spreadsheet asli: **90 baris → 15 konsep × 6 variasi** terbaca benar.
- Generate nyata di Gemini Web lewat profil login: **6/6 gambar berhasil** (2.4–2.6 MB masing-masing).
- Metadata: file hasil generate punya **XMP + IPTC + judul + 30/30 keyword** saat dibaca ulang dari byte file.
- Panel juri: **12–15 detik** untuk 4 juri paralel per gambar. Contoh temuan nyata: juri teknis menolak gambar
  saklar lampu karena anatomi tangan cacat (skor 25) — persis jenis cacat yang tidak terlihat saat thumbnail.
- Run konsep pertama: 6 generate → 3 shortlisted, 3 rejected (dipindah ke `output/_rejected`).
- Dashboard di browser: 6 kartu tampil, 6 thumbnail termuat, chip skor per juri muncul.
- Tombol Setujui → file masuk `output/_shortlist`; Buang → pindah ke `output/_rejected`.
- Run ulang dengan sheet sama: **0 gambar digenerate ulang** (md5 file tidak berubah), status kembali utuh.

## Yang tersisa untuk builder berikutnya

- `concept_limit` di config untuk uji coba sebagian; default 0 (semua konsep).
- Profil Akun 2–8 masih kosong → user perlu login manual sekali per akun.
- Belum ada pembersihan otomatis file lama di `output/` (dibiarkan agar user bisa menilai).
- Belum ada ekspor CSV manifest shortlist (metadata sudah di dalam file, jadi belum perlu).

## Cara menjalankan

```
start.bat
.venv\Scripts\python.exe -m unittest discover -s tests -t . -v   # 22 test
```
