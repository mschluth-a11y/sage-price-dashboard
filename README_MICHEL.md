
# Michel – Toppreise Web-Dashboard (GitHub Pages + Push)

**Repo-Vorschlag:** `sage-price-dashboard`  
**Pages-Branch:** `gh-pages`  
**ntfy-Topic (secret):** `michel_sage_alerts_f9v7w2yq`

## Schnellstart lokal
```bash
python -m venv .venv
# Windows: .\.venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
pip install requests beautifulsoup4
python toppreise_web_dashboard.py --serve --interval 21600
```

## GitHub Pages & Actions
1. Neues Repo `sage-price-dashboard` anlegen.
2. Branch `gh-pages` verwenden, Dateien pushen.
3. Settings → Pages: Source `gh-pages` (root) aktivieren.
4. Settings → Actions → General → Workflow permissions: **Read and write**.
5. Actions → Secrets: `NTFY_TOPIC` = `michel_sage_alerts_f9v7w2yq`.
6. Actions → Variables: `THRESHOLDS_JSON` = `{"Sage Barista Touch Impress":960, "Sage Barista Pro":580, "Sage Barista Touch":900}`.
7. Actions → „Update Dashboard“ manuell laufen lassen oder Cron abwarten.

## Dashboard-URL
`https://<dein-user>.github.io/sage-price-dashboard/`

## Android
- ntfy-App installieren, Topic **michel_sage_alerts_f9v7w2yq** abonnieren.
- Web-Widget-App: URL zur `index.html` eintragen.
