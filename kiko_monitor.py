"""
KIKO x Stabilo Lip Mark(er) restock monitor -> Discord notification.

  python kiko_monitor.py                 # normal run (used by GitHub Actions)
  python kiko_monitor.py --debug         # print what was read per shade + save screenshot, no notify
  python kiko_monitor.py --test-discord  # send a test message to Discord

Each run opens ONE collection page (all shades on one page), alternating
FR and UK. KIKO's anti-bot tends to block a second page opened right after.
"""
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

# ---- Edit these to change what is watched ---------------------------------
# Collection pages, checked one per run in rotation.
LISTINGS = [
    {
        "country": "FR",
        "url": "https://www.kikocosmetics.com/fr-fr/new-in/nouveau-stabilo-x-kiko-milano-lip-marker/",
        "locale": "fr-FR",
        "product_base": "https://www.kikocosmetics.com/fr-fr/p/",
    },
    {
        "country": "UK",
        "url": "https://www.kikocosmetics.com/en-gb/new-in/new-stabilo-x-kiko-milano-lip-marker/",
        "locale": "en-GB",
        "product_base": "https://www.kikocosmetics.com/en-gb/p/",
    },
]

# Shades to watch: (label, part of the product URL). Same slug in UK and FR.
# Others on the page: "stabilo-x-kiko-lip-marker-02-58752", "...-05-58756".
SHADES = [
    ("01 Fine Print", "stabilo-x-kiko-lip-marker-01-58749"),
    ("04 On My Radar", "stabilo-x-kiko-lip-marker-04-58755"),
]

# Text that shows up on KIKO's anti-bot block page (lowercase).
BLOCK_MARKERS = [
    "anomaly detected",
    "think that you are a bot",
    "solve this captcha",
    "radware captcha",
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
# Status summary to Discord (no @everyone). Runs are ~15 min apart and often
# late, so "55 minutes since the last summary" gives roughly one per hour.
SUMMARY_EVERY_MIN = 55
WIB = timezone(timedelta(hours=7))
# ---------------------------------------------------------------------------

STATE_FILE = Path("state/state.json")
WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()

# For each slug: find links to that product, walk up to the product card
# (the biggest ancestor that still links to only one product and has a
# button), and return the texts of the buttons in that card.
CARD_BUTTONS_JS = r"""
(slugs) => {
  const result = {};
  for (const slug of slugs) {
    const texts = [];
    for (const a of document.querySelectorAll(`a[href*="${slug}"]`)) {
      let el = a, card = null;
      for (let d = 0; el && d < 15; d++) {
        const hrefs = new Set(
          [...el.querySelectorAll("a[href*='/p/']")].map(x => (x.getAttribute("href") || "").split("?")[0])
        );
        if (hrefs.size > 1) break;
        if (el.querySelector("button")) card = el;
        el = el.parentElement;
      }
      if (!card) continue;
      for (const b of card.querySelectorAll("button, a[role=button]")) {
        const t = (b.innerText || "").trim().replace(/\s+/g, " ");
        if (t && !texts.includes(t)) texts.push(t);
      }
    }
    result[slug] = texts;
  }
  return result;
}
"""


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
    """First button that matches an OUT or IN label decides."""
    for raw in texts:
        t = " ".join(raw.split()).lower()
        if not t:
            continue
        if any(re.search(p, t) for p in OUT_PATTERNS):
            return "out", t
        if any(re.search(p, t) for p in IN_PATTERNS):
            return "in", t
    return "unknown", None


def check_listing(browser, listing, debug=False):
    """Returns ("blocked"|"error"|"ok", {slug: (status, label)})."""
    ctx = browser.new_context(
        locale=listing["locale"],
        viewport={"width": 1366, "height": 900},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
    )
    page = ctx.new_page()
    try:
        page.goto(listing["url"], wait_until="domcontentloaded", timeout=45000)
        try:
            page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:
            pass
        page.wait_for_timeout(3000)  # let the stock buttons render
        body = (page.title() + " " + page.content()).lower()
        if debug:
            page.screenshot(path=f"debug_{listing['country']}.png", full_page=True)
        if any(m in body for m in BLOCK_MARKERS):
            return "blocked", {}
        found = page.evaluate(CARD_BUTTONS_JS, [slug for _, slug in SHADES])
        results = {}
        for _, slug in SHADES:
            texts = found.get(slug, [])
            if debug:
                print(f"  {slug}: buttons={texts}")
            results[slug] = classify(texts)
        return "ok", results
    except Exception as e:
        print(f"  error on {listing['country']} page: {e}")
        return "error", {}
    finally:
        ctx.close()


STATUS_TEXT = {"out": "habis", "in": "🟢 ADA", "unknown": "❓ tidak terbaca"}


def summary(state, now):
    """Hourly status message built from the last known state of every shade."""
    lines = [f"📋 Update {datetime.fromtimestamp(now, WIB):%H:%M} WIB, monitor masih jalan."]
    for l in LISTINGS:
        c = l["country"]
        checked = state.get(f"checked:{c}")
        when = (
            f"cek terakhir {datetime.fromtimestamp(float(checked), WIB):%H:%M}"
            if checked else "belum pernah dicek"
        )
        page = state.get(f"page:{c}")
        if page and page != "ok":
            lines.append(f"{c}: 🚫 halaman {'diblokir' if page == 'blocked' else 'gagal dibuka'} ({when})")
            continue
        shades = " · ".join(
            f"{label.split()[0]} {STATUS_TEXT.get(state.get(f'{c} {slug}'), '-')}"
            for label, slug in SHADES
        )
        lines.append(f"{c}: {shades} ({when})")
    return "\n".join(lines)


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

    # One collection page per run, rotating through LISTINGS.
    try:
        i = int(state.get("_next", 0)) % len(LISTINGS)
    except (TypeError, ValueError):
        i = 0
    listing = LISTINGS[i]
    country = listing["country"]
    page_key = f"page:{country}"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page_status, results = check_listing(browser, listing, debug=debug)
        browser.close()

    prev_page = state.get(page_key)
    print(f"{country} collection page: {page_status} [before: {prev_page}]")

    if page_status != "ok":
        if not debug and page_status != prev_page:
            if page_status == "blocked":
                notify(
                    f"🚫 KIKO memblokir skrip (halaman anti-bot) di halaman {country}. "
                    f"Pengecekan {country} tidak jalan sampai blokir hilang. "
                    f"Pakai 'Notify me' resmi KIKO sebagai cadangan."
                )
            else:
                notify(f"⚠️ Skrip gagal membuka halaman {country}. Cek manual: {listing['url']}")
    else:
        for label, slug in SHADES:
            status, button = results[slug]
            key = f"{country} {slug}"
            prev = state.get(key)
            name = f"{country} {label}"
            url = f"{listing['product_base']}{slug}/"
            print(f"  {name}: {status} (button: {button}) [before: {prev}]")
            if debug:
                continue
            if status == "in" and prev != "in":
                others = "\n".join(
                    f"- {l['country']}: {l['product_base']}{slug}/"
                    for l in LISTINGS if l is not listing
                )
                notify(
                    f"@everyone 🟢 **RESTOCK!** Lip Mark(er) {name} sudah ada.\n"
                    f"Buruan checkout: {url}\n"
                    f"Cek juga negara lain:\n{others}"
                )
            elif status == "out" and prev == "in":
                notify(f"🔴 Lip Mark(er) {name} habis lagi.")
            elif status == "unknown" and prev != "unknown":
                notify(
                    f"⚠️ Skrip nggak bisa baca tombol {name} di halaman koleksi "
                    f"(tata letak berubah?). Cek manual: {url}"
                )
            state[key] = status

    if not debug:
        now = time.time()
        state[page_key] = page_status
        state[f"checked:{country}"] = now
        state["_next"] = (i + 1) % len(LISTINGS)
        try:
            last = float(state.get("_last_summary", 0))
        except (TypeError, ValueError):
            last = 0
        if now - last >= SUMMARY_EVERY_MIN * 60:
            notify(summary(state, now))
            state["_last_summary"] = now
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, indent=2))


if __name__ == "__main__":
    main()
