# -*- coding: utf-8 -*-
"""
Toppreise.ch Web-Dashboard (GitHub Pages + Widget-Ansicht + optional Push-Alerts)

Erzeugt:
- prices.json         (Daten)
- index.html          (normale Ansicht, Template: index_template.html)
- widget.html         (kompakte Ansicht fürs Homescreen-Widget, Template: widget_template.html)

Optionale Push-Alerts (ntfy):
- per config.json mit "ntfy_topic"
- ODER via Umgebungsvariablen in GitHub Actions: NTFY_TOPIC, THRESHOLDS

Hinweise:
- Keine verschachtelten <a>-Tags in der Kartenausgabe. Jede Karte ist genau EIN Link.
- Platzhalter im Template: <!--__ROWS__--> und __UPDATED__
"""

import argparse
import http.server
import json
import os
import re
import socketserver
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional

import requests
from bs4 import BeautifulSoup

# --------- HTTP / Scrape Settings ----------
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "de-CH,de;q=0.9,en;q=0.8",
}
TIMEOUT = 20
REQUEST_PAUSE = 1.8

# --------- Produkte & Quellen (deine Vorgaben) ----------
PRODUCTS: Dict[str, List[str]] = {
    # Barista Touch Impress – genau die zwei Varianten
    "Sage Barista Touch Impress": [
        "https://www.toppreise.ch/preisvergleich/Siebtraegermaschinen/SAGE-The-Barista-Touch-Impress-Cold-Brushed-Edelstahl-p816790",
        "https://www.toppreise.ch/preisvergleich/Siebtraegermaschinen/SAGE-The-Barista-Touch-Impress-Trueffelschwarz-p743055",
    ],
    # Beispiele – beliebig erweiterbar
    "Sage Barista Pro": [
        "https://www.toppreise.ch/produktserie/The_Barista_Pro-pc-s43919",
        "https://www.toppreise.ch/preisvergleich/Siebtraegermaschinen/SAGE-The-Barista-Pro-Gebuerstetes-Edelstahlgrau-p565803",
    ],
    "Sage Barista Touch": [
        "https://www.toppreise.ch/produktserie/The_Barista_Touch-pc-s43924",
        "https://www.toppreise.ch/preisvergleich/Siebtraegermaschinen/SAGE-The-Barista-Touch-Trueffelschwarz-p565822",
    ],
}

# --------- Defaults für Alarme ----------
DEFAULT_THRESHOLDS = {
    "Sage Barista Touch Impress": 960.0,
    "Sage Barista Pro": 580.0,
    "Sage Barista Touch": 900.0,
}

# --------- Regex für CHF-Preise ----------
RE_CHF_ANY = re.compile(r"CHF\s*([0-9'’_]+(?:\.[0-9]{2})?)", re.IGNORECASE)
RE_AB_CHF = re.compile(r"ab\s*CHF\s*([0-9'’_]+(?:\.[0-9]{2})?)", re.IGNORECASE)
RE_GUENSTIGSTER = re.compile(
    r"günstigster\s+Produktpreis.*?CHF\s*([0-9'’_]+(?:\.[0-9]{2})?)",
    re.IGNORECASE | re.DOTALL,
)


# ===================== Utility / Config =====================

def load_config(conf_path: str = "config.json") -> dict:
    """Lädt config.json (falls vorhanden) + Umgebungsvariablen (NTFY_TOPIC, THRESHOLDS)."""
    cfg = {}
    if os.path.exists(conf_path):
        try:
            with open(conf_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}

    # Umgebungsvariablen überschreiben config.json
    if os.getenv("NTFY_TOPIC"):
        cfg["ntfy_topic"] = os.getenv("NTFY_TOPIC")

    if os.getenv("THRESHOLDS"):
        try:
            cfg["thresholds"] = json.loads(os.getenv("THRESHOLDS"))
        except Exception:
            pass

    return cfg


def chf_to_float(val: str) -> float:
    """Wandelt 'CHF 1'099.00' -> 1099.0"""
    clean = val.replace("’", "'").replace("'", "").replace("_", "").strip()
    try:
        return float(clean)
    except ValueError:
        return float(clean.replace(",", "."))


def extract_min_price_from_text(text: str) -> Optional[float]:
    """Robustes Herausziehen eines Minimalpreises aus rohem Seiten-Text."""
    m = RE_AB_CHF.search(text)
    if m:
        try:
            return chf_to_float(m.group(1))
        except Exception:
            pass

    m = RE_GUENSTIGSTER.search(text)
    if m:
        try:
            return chf_to_float(m.group(1))
        except Exception:
            pass

    candidates = []
    for m in RE_CHF_ANY.finditer(text):
        try:
            price = chf_to_float(m.group(1))
            if 100.0 <= price <= 3000.0:
                candidates.append(price)
        except Exception:
            continue
    return min(candidates) if candidates else None


# ===================== Scraper =====================

def fetch_min_price(url: str) -> Optional[float]:
    """Liest eine URL und versucht, den minimalen plausiblen CHF-Preis zu bestimmen."""
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")
    text = soup.get_text(" ", strip=True)

    price = extract_min_price_from_text(text)
    if price is not None:
        return price

    # Fallback: Teiltexte mit Preisbezug zusammenstellen
    likely = []
    for tag in soup.find_all(True):
        try:
            t = tag.get_text(" ", strip=True)
        except Exception:
            continue
        if not t:
            continue
        if ("CHF" in t) or ("Preis" in t) or ("Angebot" in t) or ("inkl." in t) or ("ab" in t):
            likely.append(t)
    if likely:
        joined = " ".join(likely)
        price = extract_min_price_from_text(joined)
        if price is not None:
            return price

    return None


def poll_all_products() -> Dict[str, Dict[str, str]]:
    """Durchläuft alle Modelle und sammelt pro Modell den kleinsten gefundenen Preis + Quell-URL."""
    out: Dict[str, Dict[str, str]] = {}
    for model, urls in PRODUCTS.items():
        min_price = None
        min_url = None
        for u in urls:
            try:
                p = fetch_min_price(u)
            except Exception:
                p = None
            if p is not None and (min_price is None or p < min_price):
                min_price = p
                min_url = u
            time.sleep(REQUEST_PAUSE)

        out[model] = {
            "price_chf": f"{min_price:.2f}" if min_price is not None else "",
            "url": min_url or (urls[0] if urls else ""),
        }
    return out


# ===================== Output: JSON & HTML =====================

def write_json(data: Dict[str, Dict[str, str]], path: str = "prices.json"):
    payload = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "items": data,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def render_html(data: Dict[str, Dict[str, str]], template_path: str, out_path: str):
    """
    Baut HTML, indem es das Template lädt und <!--__ROWS__--> + __UPDATED__ ersetzt.

    WICHTIG: Jede Karte ist genau EIN Link (<a class="card"> ... </a>),
             keine verschachtelten <a>-Tags im Inneren.
    """
    with open(template_path, "r", encoding="utf-8") as f:
        tpl = f.read()

    rows = []
    for model, d in data.items():
        price = d.get("price_chf")
        url = d.get("url")
        price_txt = f"ab CHF {price}".replace(",", "'") if price else "—"

        # Karte als Link (einziger <a>-Tag in der Card)
        row = f"""
        {url}
          <div class="model">{model}</div>
          <div class="price">{price_txt}</div>
          <div class="link">{url}</div>
        </a>
        """
        rows.append(row)

    html = (
        tpl.replace("<!--__ROWS__-->", "\n".join(rows))
           .replace("__UPDATED__", datetime.now().strftime("%d.%m.%Y %H:%M"))
    )
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)


# ===================== Push Alerts (optional) =====================

def send_push_if_needed(data: Dict[str, Dict[str, str]], cfg: dict):
    """
    Sendet ntfy-Push, falls Preis <= Schwelle.
    - cfg['ntfy_topic']: Topic-Name (String)
    - cfg['thresholds']: Dict[Modell -> Schwelle]
    """
    topic = cfg.get("ntfy_topic")
    if not topic:
        return

    thresholds = cfg.get("thresholds", {}) or DEFAULT_THRESHOLDS
    for model, d in data.items():
        price_s = d.get("price_chf")
        if not price_s:
            continue
        try:
            price = float(price_s)
        except Exception:
            continue
        thr = thresholds.get(model)
        if thr is None:
            continue
        if price <= float(thr):
            try:
                title = f"Preis-Alarm: {model}"
                msg = f"aktuell ab CHF {price:.2f} (Schwelle: CHF {thr:.2f})\n{d.get('url','')}"
                requests.post(
                    f"https://ntfy.sh/{topic}",
                    data=msg.encode("utf-8"),
                    headers={
                        "Title": title,
                        "Priority": "high",
                        "Tags": "moneybag,chart_with_downwards_trend",
                    },
                    timeout=10,
                )
            except Exception:
                # Push-Fehler stillschweigend ignorieren
                pass


# ===================== Orchestrierung =====================

def generate_once():
    """
    Einmaliger Durchlauf:
    - Preise sammeln
    - JSON schreiben
    - HTML normal + HTML Widget aus Templates erzeugen
    - (optional) Push-Alerts
    """
    cfg = load_config()
    data = poll_all_products()
    write_json(data)
    # Normale Ansicht
    render_html(data, "index_template.html", "index.html")
    # Kompakte Widget-Ansicht
    render_html(data, "widget_template.html", "widget.html")
    # Push
    send_push_if_needed(data, cfg)


def serve_forever(port: int = 8000):
    handler = http.server.SimpleHTTPRequestHandler
    with socketserver.TCPServer(("0.0.0.0", port), handler) as httpd:
        print(f"Serving at http://localhost:{port}")
        httpd.serve_forever()


def main():
    ap = argparse.ArgumentParser(description="Toppreise Web-Dashboard")
    ap.add_argument("--serve", action="store_true", help="einfachen HTTP-Server starten (localhost:8000)")
    ap.add_argument("--port", type=int, default=8000, help="Port für --serve")
    ap.add_argument("--interval", type=int, default=0, help="Sekundenintervall für zyklisches Aktualisieren (0 = einmalig)")
    args = ap.parse_args()

    def job():
        try:
            print("[Update] Generiere Dashboard …")
            generate_once()
            print("[OK] Dashboard aktualisiert.")
        except Exception as ex:
            print("[ERR]", ex)

    # Initial
    job()

    # Modi
    if args.serve and args.interval <= 0:
        serve_forever(args.port)
    elif args.serve and args.interval > 0:
        def loop():
            while True:
                time.sleep(args.interval)
                job()
        t = threading.Thread(target=loop, daemon=True)
        t.start()
        serve_forever(args.port)
    elif args.interval > 0:
        while True:
            time.sleep(args.interval)
            job()


if __name__ == "__main__":
    main()
