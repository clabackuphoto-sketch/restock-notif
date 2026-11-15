[README.md](https://github.com/user-attachments/files/34867427/README.md)
# KIKO restock monitor → Discord

Mantau halaman produk Stabilo x KIKO Lip Mark(er) shade 01 & 04. Begitu tombol "Notify me" / "Me prévenir" berubah jadi "Add to bag", kamu dapat notif di Discord (HP kamu bunyi karena ada @everyone).

## 1. Bikin webhook Discord
1. Buat server Discord pribadi (atau pakai yang sudah ada), bikin channel misalnya `#restock`.
2. Edit Channel → Integrations → Webhooks → New Webhook → **Copy Webhook URL**.
3. Pastikan app Discord di HP: notifikasi aktif untuk server itu (set "All Messages" atau @everyone).

## 2. Pasang di GitHub (gratis, jalan tanpa laptop nyala)
1. Bikin repo baru di GitHub. **Pilih Public**: Actions di repo public gratis tanpa batas menit. (Webhook kamu aman, disimpan di Secrets, bukan di kode.) Kalau Private, jatah gratis 2000 menit/bulan akan habis, ganti cron jadi `*/15`.
2. Upload `kiko_monitor.py` dan `README.md` ke root repo.
3. Upload `monitor.yml` ke folder `.github/workflows/monitor.yml` (bikin foldernya lewat "Add file → Create new file", ketik path lengkapnya).
4. Settings → Secrets and variables → Actions → New repository secret:
   - Name: `DISCORD_WEBHOOK_URL`
   - Value: webhook URL tadi
5. Tab Actions → "KIKO restock monitor" → Run workflow. Cek lognya: harus muncul status tiap produk.

## 3. Cek dulu sebelum percaya
Di laptop (butuh Python):
```
pip install playwright
playwright install chromium
python kiko_monitor.py --debug
```
Ini mencetak semua tombol yang terbaca di halaman dan menyimpan screenshot `debug_0.png`, dst. Pastikan produk yang sedang sold out terbaca `out` (tombol "notify me"). Kalau hasilnya `unknown`, kirim output-nya ke aku, nanti pola teksnya kita sesuaikan.

Tes Discord: `DISCORD_WEBHOOK_URL="..." python kiko_monitor.py --test-discord`

## Catatan
- GitHub Actions cron sering telat 5–15 menit saat ramai. Kalau restock-nya cepat habis, jalankan skripnya di laptop/Termux pakai loop tiap 2–3 menit.
- Repo yang tidak ada aktivitas 60 hari bakal dinonaktifkan jadwalnya oleh GitHub; tinggal aktifkan lagi di tab Actions.
- Yang dipantau: UK dan FR, shade 01 dan 04 (URL sudah dicek di browser). Makin banyak halaman, makin besar peluang kena blokir, jadi cron diset tiap 15 menit dan ada jeda 8 detik antar halaman.
- Kalau KIKO menampilkan halaman anti-bot, status jadi `blocked`: skrip berhenti di run itu dan kirim satu peringatan ke Discord (tidak berulang). Skrip tidak mencoba menembus blokir. Aktifkan juga "Notify me" resmi KIKO (email) sebagai cadangan.
- Skrip ini cuma memantau dan memberi notif. Checkout tetap manual.
