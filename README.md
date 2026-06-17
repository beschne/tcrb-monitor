# T CrB Monitor

Skript zum Alarmieren, wenn T Coronae Borealis (T CrB) in der nördlichen Krone nach 80 Jahren wieder ausbricht.
Läuft gegen die AAVSO-WebObs-Datenbank (AUID 000-BBW-825). Nur Standardbibliotheken, keine externen Pakete.

## Was es macht

- Holt die jüngsten 200 Beobachtungen und schreibt sie dedupliziert in `tcrb_history.csv` (alle Bänder, für eigene Auswertung).
- Bewertet Schwellen nur auf Vis.- und V-Beobachtungen — der M-Riese macht T CrB im I-/R-Band dauerhaft ~7 mag hell, was sonst Dauerfehlalarm gäbe.
- Zwei Stufen: `--warn-mag 8.0` (auffällig) und `--erupt-mag 6.0` (Ausbruch wahrscheinlich). Alarm nur bei Eskalation; `tcrb_state.json` verhindert Mehrfachmeldungen.
- Benachrichtigung per macOS-Mitteilung (`osascript`) und optional über Signal.

## Aufruf

```bash
python3 tcrb_monitor.py             # Normalbetrieb
python3 tcrb_monitor.py --dry-run   # nur anzeigen, nichts speichern
python3 tcrb_monitor.py --test-alert  # Testnachricht über alle aktiven Kanäle
```

## Einrichtung

Zuerst Skript und Plist an einen festen Ort legen und in der Plist alle `/Users/USERNAME/Scripts/tcrb`-Pfade durch deine echten ersetzen (Username, Zielordner). `which python3` zeigt dir den richtigen Interpreter-Pfad — `/usr/bin/python3` ist das Apple-System-Python und reicht, da das Skript nur Standardbibliothek nutzt.

```bash
mkdir -p ~/Scripts/tcrb
cp tcrb_monitor.py ~/Scripts/tcrb/
cp config.py ~/Scripts/tcrb/          # Secrets; launchd lädt aus dem WorkingDirectory
cp de.agorion.tcrb.plist ~/Library/LaunchAgents/

# Syntax prüfen (gibt nichts aus, wenn ok)
plutil -lint ~/Library/LaunchAgents/de.agorion.tcrb.plist

# laden (moderne Syntax; "gui/$(id -u)" ist deine Login-Session)
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/de.agorion.tcrb.plist

# sofort einmal testen, ohne auf die volle Stunde zu warten
launchctl kickstart -k gui/$(id -u)/de.agorion.tcrb
```

Prüfen, ob er läuft und was er tut:

```bash
launchctl print gui/$(id -u)/de.agorion.tcrb | grep -i state
cat ~/Scripts/tcrb/tcrb.log
```

Bei Änderungen an der Plist erst entladen, dann neu laden:

```bash
launchctl bootout gui/$(id -u)/de.agorion.tcrb
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/de.agorion.tcrb.plist
```

## Zwei Hinweise

- Die Plist feuert stündlich zur vollen Stunde, rund um die Uhr — sinnvoll, weil Ausbruchsmeldungen weltweit jederzeit eintreffen und T CrB nach dem Peak schnell wieder verblasst. Wenn dir das zu oft ist, kannst du `StartCalendarInterval` auch als Array mehrerer Uhrzeiten schreiben (z. B. nur 6, 12, 18, 22 Uhr).
- Die macOS-Mitteilung per `osascript` läuft aus dem LaunchAgent heraus in deiner GUI-Session und erscheint normalerweise problemlos; beim ersten Mal musst du eventuell in den Systemeinstellungen unter Mitteilungen die Berechtigung bestätigen. Falls dir die Mitteilung als Kanal zu unzuverlässig ist, ist der Signal-Versand der robustere Weg — beim Ausbruch zählt jede Stunde.

## Alarmmeldung über Signal

In `config.py` (Vorlage: `config.sample.py`) zu füllen:

- `SIGNAL_CLI` — Pfad prüfen mit `which signal-cli` (Apple Silicon meist `/opt/homebrew/bin/signal-cli`)
- `SIGNAL_ACCOUNT` — deine verknüpfte Nummer
- Entweder `SIGNAL_GROUP_ID` (hat Vorrang) oder `SIGNAL_RECIPIENTS`
- `SIGNAL_ENABLED` steht im Skriptkopf und ist standardmäßig `True`; fehlt `config.py`, deaktiviert sich Signal automatisch.

Zwei Dinge noch: Der signal-cli-Pfad muss absolut sein, da launchd nur einen minimalen PATH kennt. Und signal-cli schreibt seinen Zustand nach `~/.local/share/signal-cli` — da der LaunchAgent unter deinem User läuft, passt das ohne Zusatzkonfiguration.

## Einrichten von signal-cli

```bash
brew install signal-cli
signal-cli link -n "TCrB-Monitor"          # zeigt einen QR-Code; im Handy unter Einstellungen → Verknüpfte Geräte scannen
signal-cli -u +49DEINENUMMER receive       # einmal, holt Kontakte und Gruppen
signal-cli -u +49DEINENUMMER listGroups    # liefert die base64-Gruppen-ID
```

Verknüpfen (statt Registrieren) ist der bequemere Weg: dein Handy bleibt das Hauptgerät, der Mac wird nur ein Zweitgerät.

## Testen

```bash
python3 tcrb_monitor.py --test-alert
```

Das schickt eine klar als Test markierte Nachricht über alle aktiven Kanäle und beendet sich, ohne den Status zu verändern — ideal, um vor dem Scharfschalten zu prüfen, ob Signal wirklich ankommt.

## Links

- [T CrB aktuell – TheSkyLive](https://theskylive.com/sky/stars/hr-5958-star) — Live-Helligkeit und aktuelle Informationen zu T Coronae Borealis
- [AAVSO Photometrie-Datenbanksuche](https://apps.aavso.org/v2/data/search/photometry/) — Rohdaten aller AAVSO-Beobachtungen durchsuchen
