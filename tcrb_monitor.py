#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tcrb_monitor.py  --  Helligkeitsueberwachung von T CrB ("Blaze Star")

Holt die juengsten Beobachtungen aus der AAVSO-WebObs-Datenbank, schreibt sie
in eine lokale CSV-Historie und schlaegt Alarm, sobald die Helligkeit eine
einstellbare Schwelle ueberschreitet (= Stern wird heller -> kleinere mag-Zahl).

Reine Standardbibliothek -- keine externen Pakete noetig. Getestet mit Python 3.9+.

Gedacht fuer den taeglichen (oder stuendlichen) Aufruf via cron/launchd.

Quelle:  https://www.aavso.org/apps/webobs/results/   (oeffentlich, kein Login)
AUID T CrB: 000-BBW-825

Diese ist die Version 2 ohne Mail-Versand. Stattdessen Versand über Signal.
"""

import argparse
import csv
import datetime as dt
import html as ihtml
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

# --------------------------------------------------------------------------
# KONFIGURATION  (kann komplett per Kommandozeile/ENV ueberschrieben werden)
# --------------------------------------------------------------------------
STAR          = "T CrB"
NUM_RESULTS   = 200          # wie viele juengste Beobachtungen abrufen
OBS_TYPES     = "vis+ccd"    # visuelle + CCD/CMOS-Beobachtungen

# Alarmschwellen (mag). Heller = kleinere Zahl. Ruhe von T CrB liegt bei ~10.
WARN_MAG      = 8.0          # Fruehwarnung: ungewoehnlich hell, Auge drauf
ERUPT_MAG     = 6.0          # Ausbruch sehr wahrscheinlich -> sofort losfahren

# WICHTIG: Nur diese Baender fuer den Alarm auswerten.
# T CrB ist ein symbiotischer Stern mit M-Riese -> im Infrarot (I, R) dauerhaft
# hell (~6-7 mag), obwohl der Stern visuell in Ruhe bei ~10 mag steht. Wuerde man
# alle Baender gleich behandeln, gaebe es staendig Fehlalarme. Fuer "wird mit
# blossem Auge sichtbar" zaehlen visuelle und Johnson-V-Schaetzungen.
ALERT_BANDS   = {"Vis.", "V"}

# Dateien (Vorgabe: neben dem Skript; passt zum Offline-Workflow)
BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
CSV_PATH      = os.path.join(BASE_DIR, "tcrb_history.csv")
STATE_PATH    = os.path.join(BASE_DIR, "tcrb_state.json")

# Benachrichtigung
USE_MACOS_NOTIFY = True       # macOS-Mitteilung via osascript

# --- Signal via signal-cli ------------------------------------------------
# Voraussetzung: signal-cli installiert und EINMALIG eingerichtet, z. B.
#   brew install signal-cli
#   signal-cli link -n "TCrB-Monitor"      # QR mit dem Handy scannen
# Eigene Gruppen-IDs (base64) auflisten mit:
#   signal-cli -u +49NUMMER listGroups
SIGNAL_ENABLED    = True
SIGNAL_CLI        = "/opt/homebrew/bin/signal-cli"  # Apple Silicon; Intel: /usr/local/bin
SIGNAL_ACCOUNT    = ""        # aus config.py laden
SIGNAL_GROUP_ID   = ""        # base64-Gruppen-ID (Vorrang, falls gesetzt)
SIGNAL_RECIPIENTS = []        # sonst: eine oder mehrere Nummern

# Secrets aus config.py laden (nicht im Repo, siehe config.sample.py)
try:
    import config as _cfg
    SIGNAL_CLI        = getattr(_cfg, "SIGNAL_CLI",        SIGNAL_CLI)
    SIGNAL_ACCOUNT    = getattr(_cfg, "SIGNAL_ACCOUNT",    SIGNAL_ACCOUNT)
    SIGNAL_GROUP_ID   = getattr(_cfg, "SIGNAL_GROUP_ID",   SIGNAL_GROUP_ID)
    SIGNAL_RECIPIENTS = getattr(_cfg, "SIGNAL_RECIPIENTS", SIGNAL_RECIPIENTS)
except ImportError:
    if SIGNAL_ENABLED:
        print("Hinweis: config.py fehlt – Signal deaktiviert.", file=sys.stderr)
    SIGNAL_ENABLED = False

# AAVSO WebObs URL und User-Agent
WEBOBS_URL = "https://www.aavso.org/apps/webobs/results/"
USER_AGENT = "AGO-TCrB-Monitor/1.0 (Volkssternwarte Hochtaunus)"

# --------------------------------------------------------------------------
# Datenabruf + Parsing
# --------------------------------------------------------------------------
def fetch_observations(star=STAR, num=NUM_RESULTS, obs_types=OBS_TYPES):
    """Liefert eine Liste von dicts: jd, date, mag, band, observer, fainter_than."""
    # obs_types nutzt '+' als Trennzeichen -> NICHT als %2B kodieren.
    qs = (f"star={urllib.parse.quote_plus(star)}"
          f"&num_results={int(num)}"
          f"&obs_types={obs_types}")
    url = WEBOBS_URL + "?" + qs
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            page = r.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError) as e:
        print(f"AAVSO-Abruf fehlgeschlagen: {e}", file=sys.stderr)
        return []

    idx = page.find("Calendar Date")
    if idx < 0:
        print("AAVSO-Seitenstruktur geaendert: Anker 'Calendar Date' fehlt.", file=sys.stderr)
        return []
    seg = page[idx:]
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", seg, re.S)

    obs = []
    for tr in rows:
        if star.replace(" ", "") not in tr.replace(" ", ""):
            continue
        tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
        if len(tds) < 7:
            continue
        try:
            jd_raw   = _txt(tds[2])
            date_raw = _txt(tds[3])
            mag_raw  = _txt(tds[4])      # sichtbarer Text des Links = Magnitude
            band     = _txt(tds[6])
            observer = _txt(tds[7]) if len(tds) > 7 else ""

            fainter = mag_raw.startswith("<")        # "<13.5" = schwaecher als
            mag_clean = mag_raw.lstrip("<>").strip()
            mag = float(mag_clean)
            jd  = float(jd_raw)
        except (ValueError, IndexError):
            continue
        obs.append({
            "jd": jd, "date": date_raw, "mag": mag,
            "band": band, "observer": observer, "fainter_than": fainter,
        })
    return obs

def _txt(cell):
    """HTML-Zelle -> sauberer Text (Tags raus, Entities aufloesen)."""
    return ihtml.unescape(re.sub(r"<.*?>", "", cell, flags=re.S)).strip()

# --------------------------------------------------------------------------
# Auswertung
# --------------------------------------------------------------------------
def brightest_real(obs, bands=ALERT_BANDS):
    """Hellste *echte* Messung in den relevanten Baendern (keine 'fainter-than'-
    Limits, kein Infrarot). Gibt None zurueck, falls nichts Passendes vorliegt."""
    real = [o for o in obs if not o["fainter_than"] and o["band"] in bands]
    return min(real, key=lambda o: o["mag"]) if real else None

def _csv_safe(s):
    """Verhindert CSV-Formel-Injection in Tabellenkalkulationen."""
    s = str(s)
    return "'" + s if s and s[0] in ("=", "+", "-", "@", "\t", "\r") else s

def append_csv(obs, path=CSV_PATH):
    """Neue Beobachtungen anhand der JD in die CSV-Historie schreiben (dedupliziert)."""
    seen = set()
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                seen.add(row["jd"])
    new = [o for o in obs if f"{o['jd']:.5f}" not in seen]
    write_header = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(["jd", "date", "mag", "band", "observer", "fainter_than"])
        for o in sorted(new, key=lambda x: x["jd"]):
            w.writerow([f"{o['jd']:.5f}", _csv_safe(o["date"]), o["mag"],
                        _csv_safe(o["band"]), _csv_safe(o["observer"]),
                        int(o["fainter_than"])])
    return len(new)

def load_state(path=STATE_PATH):
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            print(f"Warnung: {path} unleserlich, setze Zustand zurueck.", file=sys.stderr)
    return {"level": "quiescent", "last_jd": 0.0}

def save_state(state, path=STATE_PATH):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)

# --------------------------------------------------------------------------
# Benachrichtigung
# --------------------------------------------------------------------------
def macos_notify(title, message):
    if not USE_MACOS_NOTIFY:
        return
    # Strings als argv uebergeben, nicht in den AppleScript-Quelltext einbetten,
    # um AppleScript-Injection durch gescrapte Beobachterdaten zu verhindern.
    script = (
        "on run argv\n"
        "  display notification (item 1 of argv)"
        " with title (item 2 of argv) sound name \"Glass\"\n"
        "end run"
    )
    try:
        subprocess.run(
            ["osascript", "-e", script, message, title],
            check=False,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

def send_signal(text):
    """Sendet eine Nachricht ueber signal-cli an eine Gruppe oder an Nummern.
    Schlaegt nicht das Skript tot, falls signal-cli fehlt oder fehlerhaft ist --
    die macOS-Mitteilung bleibt als zweiter Kanal bestehen."""
    if not SIGNAL_ENABLED:
        return
    if SIGNAL_GROUP_ID:
        targets = ["-g", SIGNAL_GROUP_ID]
    else:
        targets = list(SIGNAL_RECIPIENTS)
    if not targets:
        print("Signal: kein Ziel konfiguriert -- uebersprungen.", file=sys.stderr)
        return
    cmd = [SIGNAL_CLI, "-u", SIGNAL_ACCOUNT, "send", "-m", text, "--"] + targets
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if res.returncode != 0:
            print(f"Signal-Versand fehlgeschlagen (rc={res.returncode}): "
                  f"{res.stderr.strip()}", file=sys.stderr)
    except FileNotFoundError:
        print(f"Signal: '{SIGNAL_CLI}' nicht gefunden -- Pfad pruefen.",
              file=sys.stderr)
    except subprocess.TimeoutExpired:
        print("Signal: Zeitueberschreitung beim Senden.", file=sys.stderr)

def recent_context(obs, bands=ALERT_BANDS, window_days=1.0, max_points=5):
    """Kurzer Trendblock fuer die Alarmmeldung: Spanne und Aenderungsrate der
    relevanten Messungen im Zeitfenster (Default 24 h ab juengster Beobachtung)
    plus die juengsten Einzelpunkte. Liefert mehrzeiligen Text."""
    real = [o for o in obs if not o["fainter_than"] and o["band"] in bands]
    if not real:
        return "Kein Vis./V-Kontext verfuegbar."
    real.sort(key=lambda o: o["jd"], reverse=True)   # neueste zuerst
    newest_jd = real[0]["jd"]
    window = [o for o in real if o["jd"] >= newest_jd - window_days]

    mags = [o["mag"] for o in window]
    hell, schwach = min(mags), max(mags)
    lines = [f"Letzte {window_days*24:.0f} h: {len(window)} Vis./V-Messung(en), "
             f"{hell:.2f}-{schwach:.2f} mag (Ruhe ~10)."]

    # Aenderungsrate ueber das Fenster (negativ = wird heller)
    if len(window) >= 2:
        delta_days = window[0]["jd"] - window[-1]["jd"]
        dmag = window[0]["mag"] - window[-1]["mag"]
        if delta_days > 0:
            rate = dmag / delta_days
            richtung = "heller" if dmag < 0 else ("schwaecher" if dmag > 0 else "stabil")
            lines.append(f"Tendenz: {abs(dmag):.2f} mag {richtung} in "
                         f"{delta_days*24:.1f} h  (~{abs(rate):.2f} mag/Tag).")

    lines.append("Juengste Punkte:")
    for o in real[:max_points]:
        obs_name = o["observer"] or "?"
        lines.append(f"  {o['date']}  {o['mag']:.2f} {o['band']}  ({obs_name})")
    return "\n".join(lines)

def alert(level, obs, brightest):
    icon = {"warn": "[!] T CrB AUFFAELLIG", "erupt": "[!!!] T CrB AUSBRUCH?"}[level]
    line = (f"{brightest['mag']:.2f} mag ({brightest['band']}) "
            f"am {brightest['date']}  -- Beob. {brightest['observer']}")
    body = (f"{icon}\n\n"
            f"Hellste aktuelle Messung: {line}\n\n"
            f"{recent_context(obs)}\n\n"
            f"Live-Lichtkurve: https://app.aavso.org/v2/lcg/  (Stern: T CRB)\n"
            f"Kampagne #875:  https://forums.aavso.org/t/observing-campaigns-875-monitoring-t-crb/946\n\n"
            f"Sternbild Corona Borealis -- bei Ausbruch ~2-3 mag, mit blossem Auge sichtbar.")
    print(body)
    macos_notify(icon, line)
    send_signal(body)

# --------------------------------------------------------------------------
# Hauptablauf
# --------------------------------------------------------------------------
def run(warn_mag, erupt_mag, dry_run=False):
    obs = fetch_observations()
    if not obs:
        print("Keine Beobachtungen erhalten -- Endpunktstruktur evtl. geaendert?",
              file=sys.stderr)
        return 2

    n_new = 0 if dry_run else append_csv(obs)
    b = brightest_real(obs)
    if b is None:
        print("Keine auswertbaren Vis./V-Messungen in den juengsten Daten "
              "(nur Limits oder andere Baender) -- nichts zu melden.")
        return 0

    state = load_state()
    prev_level = state.get("level", "quiescent")

    if b["mag"] <= erupt_mag:
        level = "erupt"
    elif b["mag"] <= warn_mag:
        level = "warn"
    else:
        level = "quiescent"

    rank = {"quiescent": 0, "warn": 1, "erupt": 2}
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"[{now}] hellste echte Messung: {b['mag']:.2f} {b['band']} "
          f"({b['date']}) | Status: {level} | neue Datensaetze: {n_new}")

    # Alarm nur bei *Verschlechterung* der Stufe (Entprellung gegen Mehrfachmeldungen)
    if rank[level] > rank[prev_level] and level in ("warn", "erupt"):
        alert(level, obs, b)

    if not dry_run:
        state["level"] = level
        state["last_jd"] = max(o["jd"] for o in obs)
        save_state(state)
    return 0

def parse_args():
    p = argparse.ArgumentParser(description="AAVSO-Helligkeitsmonitor fuer T CrB")
    p.add_argument("--warn-mag", type=float, default=WARN_MAG,
                   help=f"Fruehwarnschwelle in mag (Vorgabe {WARN_MAG})")
    p.add_argument("--erupt-mag", type=float, default=ERUPT_MAG,
                   help=f"Ausbruchsschwelle in mag (Vorgabe {ERUPT_MAG})")
    p.add_argument("--dry-run", action="store_true",
                   help="nur abrufen/anzeigen, nichts speichern oder alarmieren")
    p.add_argument("--test-alert", action="store_true",
                   help="Test-Benachrichtigung ueber alle Kanaele senden und beenden")
    return p.parse_args()

if __name__ == "__main__":
    args = parse_args()
    if args.test_alert:
        msg = ("[TEST] T CrB-Monitor: Benachrichtigung funktioniert. "
               "Kein Ausbruch -- dies ist nur ein Verbindungstest.")
        print(msg)
        macos_notify("[TEST] T CrB-Monitor", "Signal-/Mitteilungstest erfolgreich.")
        send_signal(msg)
        sys.exit(0)
    sys.exit(run(args.warn_mag, args.erupt_mag, dry_run=args.dry_run))