"""
KIKO x Stabilo Lip Mark(er) restock monitor -> Discord notification.

  python kiko_monitor.py                 # normal run (used by GitHub Actions)
  python kiko_monitor.py --debug         # print every button found + save screenshots, no notify
  python kiko_monitor.py --test-discord  # send a test message to Discord
"""
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

# ---- Edit this list to add/remove things to watch -------------------------
PRODUCTS = [
    {
        "name": "Lip Marker 01 Fine Print (UK)",
        "url": "https://www.kikocosmetics.com/en-gb/p/stabilo-x-kiko-lip-marker-01-58749/",
        "locale": "en-GB",
    },
    {
        "name": "Lip Marker 04 On My Radar (UK)",
        "url": "https://www.kikocosmetics.com/en-gb/p/stabilo-x-kiko-lip-marker-04-58755/",
        "locale": "en-GB",
    },
    # FR URLs confirmed in a browser; sold-out label is "ME PRÉVENIR".
    {
        "name": "Lip Marker 01 Fine Print (FR)",
        "url": "https://www.kikocosmetics.com/fr-fr/p/stabilo-x-kiko-lip-marker-01-58749/",
        "locale": "fr-FR",
    },
    {
        "name": "Lip Marker 04 (FR)",
        "url": "https://www.kikocosmetics.com/fr-fr/p/stabilo-x-kiko-lip-marker-04-58755/",
        "locale": "fr-FR",
    },
]

# Pause between pages so the 4 requests aren't fired back to back.
DELAY_BETWEEN_PAGES_SEC = 8

# Text that shows up on KIKO's anti-bot block page (lowercase).
BLOCK_MARKERS = [
    "anomaly detected",
    "think that you are a bot",
    "solve this captcha",
]

# Button texts (lowercase regex). "Notify me" / "Me prévenir" confirmed as sold-out labels.
OUT_PATTERNS = [
    r"notify me",
    r"me pr[ée]venir",
    r"sold out",
    r"out of stock",
    r"[ée]puis[ée]",
    r"rupture",
]
IN_PATTERNS = [
    r"add to (bag|cart|basket)",
    r"ajouter au (panier|sac)",
    r"ajouter à mon (panier|sac)",
]
# ---------------------------------------------------------------------------

STATE_FILE = Path("state/state.json")
WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()


def notify(message: str) -> None:
    print(f"[notify] {message}")
    if not WEBHOOK:
        print("  (DISCORD_WEBHOOK_URL not set, skipping Discord)")
        return
    data = json.dumps({"content": message}).encode()
    req = urllib.request.Request(
        WEBHOOK,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "kiko-monitor/1.0"},
    )
    try:
        urllib.request.urlopen(req, timeout=20).read()
    except Exception as e:  # don't crash the whole run over a notif failure
        print(f"  Discord send failed: {e}")


def classify(texts):
    """First button (in page order) that matches an OUT or IN label decides."""
    for raw in texts:
        t = " ".join(raw.split()).lower()
        if not t:
            continue
        if any(re.search(p, t) for p in OUT_PATTERNS):
            return "out", t
        if any(re.search(p, t) for p in IN_PATTERNS):
            return "in", t
    return "unknown", None


def check(browser, product, debug=False, idx=0):
    ctx = browser.new_context(
        locale=product["locale"],
        viewport={"width": 1366, "height": 900},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
    )
    page = ctx.new_page()
    try:
        page.goto(product["url"], wait_until="domcontentloaded", timeout=45000)
        try:
            page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:
            pass
        page.wait_for_timeout(3000)  # let the stock button render
        body = page.content().lower()
        if any(m in body for m in BLOCK_MARKERS):
            if debug:
                print(f"\n== {product['name']}\n  BLOCKED by anti-bot page")
                page.screenshot(path=f"debug_{idx}.png", full_page=False)
            return "blocked", None
        texts = page.locator("button, a[role=button]").all_inner_texts()
        if debug:
            print(f"\n== {product['name']}")
            for t in texts:
                t = " ".join(t.split())
                if t:
                    print("  button:", t)
            page.screenshot(path=f"debug_{idx}.png", full_page=False)
        return classify(texts)
    except Exception as e:
        print(f"  error on {product['name']}: {e}")
        return "unknown", None
    finally:
        ctx.close()


def main():
    if "--test-discord" in sys.argv:
        notify("✅ Tes notif dari KIKO restock monitor. Kalau ini muncul, setup Discord sudah benar.")
        return

    debug = "--debug" in sys.argv
    state = {}
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text())
        except Exception:
            state = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for i, prod in enumerate(PRODUCTS):
            if i > 0:
                time.sleep(DELAY_BETWEEN_PAGES_SEC)
            status, label = check(browser, prod, debug=debug, idx=i)
            prev = state.get(prod["url"])
            print(f"{prod['name']}: {status} (button: {label}) [before: {prev}]")
            if status == "blocked":
                # Don't keep hitting the site; alert once, keep old state for the rest.
                if not debug and prev != "blocked":
                    notify(
                        f"🚫 KIKO memblokir skrip (halaman anti-bot) di {prod['name']}. "
                        f"Notif restock tidak jalan sampai blokir hilang. "
                        f"Pakai 'Notify me' resmi KIKO sebagai cadangan."
                    )
                if not debug:
                    state[prod["url"]] = status
                print("  blocked, stopping this run.")
                break
            if debug:
                continue

            if status == "in" and prev != "in":
                notify(
                    f"@everyone 🟢 **RESTOCK!** {prod['name']} sudah ada.\n"
                    f"Buruan checkout: {prod['url']}"
                )
            elif status == "out" and prev == "in":
                notify(f"🔴 {prod['name']} habis lagi.")
            elif status == "unknown" and prev != "unknown":
                notify(
                    f"⚠️ Skrip nggak bisa baca status {prod['name']} "
                    f"(halaman berubah / diblokir / URL salah?). Cek manual: {prod['url']}"
                )
            state[prod["url"]] = status
        browser.close()

    if not debug:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, indent=2))


if __name__ == "__main__":
    main()

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
