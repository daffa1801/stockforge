# StockForge

Otomasi harian untuk Adobe Stock: ambil prompt dari Google Spreadsheet, generate gambar di Gemini Web
lewat Chrome yang sudah login, tanam judul + keyword ke file, lalu saring dengan panel 4 juri AI.
Yang lolos muncul di dashboard untuk kamu setujui dan upload manual.

Untuk melanjutkan pengembangan, baca [`docs/DEVELOPER.md`](docs/DEVELOPER.md) (peta kode, API, resep
penambahan fitur, dan daftar jebakan).

## Kebutuhan

- Windows 10/11 (jalur Chrome & `.bat` dibuat untuk Windows)
- Python 3.10+ — diuji di 3.12
- Google Chrome terpasang
- Akun Google yang bisa memakai Gemini Web
- Endpoint 9router (atau OpenAI-compatible) untuk panel juri — opsional kalau hanya ingin generate

## Instalasi

```
git clone <url-repo-ini> stockforge
cd stockforge
install.bat
```

`install.bat` membuat `.venv` dan memasang `playwright` + `pillow`. Playwright di sini memakai Chrome
yang sudah terpasang di sistem, bukan mengunduh browser sendiri — tidak ada unduhan besar.

Lalu siapkan konfigurasi:

```
copy config.example.json config.json
```

Yang perlu diisi di `config.json`:

- `router.api_key` — API key 9router kamu. **File ini di-ignore git**, tidak akan pernah ter-commit.
- `router.url` — kalau 9router tidak di `localhost:20128`.
- `profiles[]` — satu entri per akun Google. Folder profil dibuat otomatis saat pertama dijalankan;
  login manual sekali per akun lewat jendela Chrome yang dibuka aplikasi.

Tanpa `config.json`, aplikasi tetap menyala memakai nilai default (panel juri tidak akan bisa memanggil
model sampai key diisi).

## Cara pakai

1. Dobel-klik **`start.bat`** (dashboard terbuka otomatis di `http://127.0.0.1:8770`).
2. Tempel link spreadsheet hari itu, tekan **Mulai**.
3. Chrome terbuka dengan profil Akun 1. Biarkan jendela itu terbuka — jangan diutak-atik saat generate.
4. Kalau satu akun kena limit Gemini, dashboard menampilkan peringatan. Ganti akun di jendela Chrome
   (atau pilih profil lain di dropdown lalu **Ganti Profil**), kemudian tekan **Lanjut**.
5. Setelah panel juri selesai: review kartu yang lolos → **Setujui** (masuk `output/_shortlist`),
   **Buang**, atau **Ulangi**.
6. Upload manual ke Adobe Stock dari folder `output/_shortlist`. Judul dan keyword sudah tertanam di file,
   jadi form Adobe terisi otomatis. Kategori ditampilkan di kartu.

## Yang terjadi di belakang

```
Spreadsheet → kelompokkan per Ide Konsep (6 variasi)
   ↓ per konsep
Generate di Gemini Web (browser, bukan API) → output/<Filename>
   ↓
Tanam XMP + IPTC + EXIF (judul, 30 keyword, deskripsi)
   ↓
Panel 4 juri vision (paralel, ~15 detik per gambar):
   event legibility · kelayakan jual · teknis & compliance · kecocokan metadata
   → butuh 3 dari 4 setuju
   ↓
Peringkat per konsep: ambil maksimal 3 terbaik dari 6 variasi
   ↓
Lolos → dashboard · Tidak lolos → output/_rejected (tidak dihapus)
```

Kalau satu konsep tidak ada yang lolos, tetap diambil 1 terbaik dan ditandai **low confidence**,
supaya kamu masih punya bahan untuk dilihat.

Keputusanmu diingat. Kalau sheet yang sama dijalankan lagi, gambar yang sudah **disetujui** atau
**dibuang** tidak digenerate ulang — hanya yang masih `pending` yang dikerjakan.

## Log pekerjaan

Setiap kejadian dicatat ke `logs/worklog-YYYY-MM-DD.jsonl` (satu baris JSON per kejadian, append-only)
dan ditampilkan di tab **Log** — tab tersendiri di sebelah tab Gambar, bukan panel kecil yang menutupi layar.

Isi tab Log:

- **Ringkasan hari itu** sebagai kartu angka: jumlah run, gambar jadi, lolos panel, dibuang panel,
  dinilai, kamu setujui, generate gagal, kena limit.
- **Filter** per jenis: Semua, Generate, Panel juri, Keputusanmu, Sistem.
- **Pencarian** bebas atas pesan, nama file, dan nama kejadian.
- **Dikelompokkan per run**: tiap run satu kartu dengan durasi, jumlah gambar, hasil, dan status
  (selesai / dihentikan / error). Klik judul run untuk melipat.
- **Pemilih tanggal** untuk melihat hari sebelumnya, dan tautan **Unduh .jsonl**.

Yang dicatat: run mulai/selesai, jumlah baris & konsep, gambar yang dilewati karena sudah diputuskan,
Chrome dibuka, login diperlukan, ganti profil, limit akun, hasil generate (ukuran file), status metadata,
penilaian tiap gambar, peringkat tiap konsep, keputusanmu (setujui/buang/ulangi), jeda, stop, dan error.

Log tidak pernah menghapus dirinya sendiri, jadi riwayat kerja tetap utuh untuk diperiksa nanti.

> Kalau tampilan terasa tidak berubah setelah update, lakukan hard reload sekali (Ctrl+Shift+R).

## Struktur folder

```
app/config.py        konfigurasi + path
app/sheets.py        baca spreadsheet, kelompokkan per konsep
app/gemini_automator.py  Playwright + Chrome profil login (generate & download)
app/metadata.py      tanam + verifikasi XMP/IPTC
app/judges.py        panel juri 9router
app/ranking.py       aturan 1-3 terbaik per konsep
app/pipeline.py      mesin state: generate → metadata → juri → peringkat
app/worklog.py       log pekerjaan harian (JSONL)
app/server.py        server dashboard + API
web/                 dashboard satu halaman
logs/                worklog-YYYY-MM-DD.jsonl            (lokal, di-ignore)
docs/DEVELOPER.md    panduan pengembangan (peta kode, API, jebakan)
docs/specs/          spesifikasi desain
output/              hasil generate                      (lokal, di-ignore)
output/_shortlist/   yang kamu setujui
output/_rejected/    yang dibuang panel
profiles/akun-N/     profil Chrome tiap akun             (lokal, di-ignore)
tests/               unit test
config.example.json  contoh konfigurasi (aman di-commit)
```

## Keamanan

- Dashboard hanya mendengarkan di `127.0.0.1`, tidak terbuka ke jaringan. Tidak ada autentikasi —
  jangan ubah bind address tanpa menambahkan proteksi.
- `config.json` berisi API key dan **di-ignore git**. Yang di-commit hanya `config.example.json` tanpa key.
- Folder `profiles/` menyimpan sesi login Google (cookie). Jangan pernah di-commit atau dibagikan.
- `output/`, `logs/`, dan `state.json` juga di-ignore: isinya pekerjaan dan data harian kamu.


## Konfigurasi penting (`config.json`)

| Kunci | Arti |
|---|---|
| `profiles` | daftar akun Chrome. Ganti `dir` ke folder profil yang sudah login. |
| `concept_limit` | batasi jumlah konsep per run (0 = semua). Pakai `1` untuk uji coba. |
| `vote_threshold` | jumlah juri yang harus setuju (default 3 dari 4). |
| `max_per_concept` | maksimal gambar yang disimpan per konsep (default 3). |
| `judges` | model tiap juri. Bisa diganti tanpa menyentuh kode. |
| `router` | URL + API key 9router (dipakai hanya untuk juri, bukan generate gambar). |
| `delay_between_prompts_sec` | jeda antar prompt supaya tidak dianggap spam. |

## Panel juri

| Juri | Model default |
|---|---|
| Event legibility | `ag/gemini-3.8-flash` |
| Kelayakan jual | `1/glm-5.3-flash` |
| Teknis & compliance | `1/deepseek-v4.1-flash` |
| Kecocokan metadata | `ag/gemini-3.8-flash-low` |

Model `1/gpt-*` dan `1/kimi-*` tidak dipakai karena tidak melayani gambar lewat 9router,
dan `ag/claude-*` menerima gambar tapi menjawab "tidak ada foto" — sudah diuji.

## Test

```
.venv\Scripts\python.exe -m unittest discover -s tests -t . -v
```

## Kalau bermasalah

- **"Port 8770 sudah dipakai"** → masih ada StockForge lain jalan. Tutup dulu, atau ubah `port`.
- **Chrome langsung tertutup / profil terkunci** → pastikan tidak ada Chrome lain memakai folder profil itu.
- **Login hilang** → login manual sekali di jendela Chrome yang dibuka StockForge; profil menyimpan sesi.
- **Juri error semua** → cek 9router hidup di `http://localhost:20128`, dan `router.api_key` masih valid.
- **Gambar gagal terus** → cek `output/` apakah ada file 0 byte; hapus lalu jalankan ulang (resume otomatis).
