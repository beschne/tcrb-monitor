# T CrB Monitor

*[English](README.md)*

Benachrichtigung, wenn [T Coronae Borealis](https://en.wikipedia.org/wiki/T_Coronae_Borealis) (T CrB, der „Blaze Star") nach 80 Jahren Ruhe ausbricht.
Fragt die [AAVSO](https://en.wikipedia.org/wiki/American_Association_of_Variable_Star_Observers) WebObs-Datenbank ab (AUID 000-BBW-825). Nur Standardbibliothek, keine externen Pakete.

T CrB ist ein Doppelsternsystem etwa 3.000 Lichtjahre entfernt: ein aufgeblähter [roter Riese](https://en.wikipedia.org/wiki/Red_giant), der langsam seine äußeren Schichten auf einen dichten [weißen Zwerg](https://en.wikipedia.org/wiki/White_dwarf) abgibt. Über Jahrtausende häuft sich das geraubte Wasserstoff auf der Oberfläche des weißen Zwergs an, bis es einen kritischen Druck und eine kritische Temperatur erreicht – dann zündet es alles auf einmal in einer thermonuklearen Explosion, einer sogenannten Nova. Der Stern erstrahlt kurz von etwa 10. Magnitude (mit bloßem Auge unsichtbar) auf rund 2. Magnitude, vergleichbar mit dem Polarstern, bevor er in den folgenden Wochen wieder verblasst. Das letzte Mal geschah dies 1946; davor 1866.

## Was es tut

- Holt die letzten 200 Beobachtungen und hängt sie dedupliziert an `tcrb_history.csv` an (alle Bänder, zur eigenen Auswertung). Die AAVSO International Database enthält Beobachtungen der British Astronomical Association, Variable Star Section (BAAVSS, fusioniert Dezember 2014) sowie der AFOEV (Association Française des Observateurs d'Étoiles Variables, laufende Kooperation) – diese sind automatisch abgedeckt.
- Wertet Alarmschwellen nur auf Vis.- und V-Beobachtungen aus – der M-Riese-Begleiter hält T CrB dauerhaft bei ~7 mag in den I/R-Bändern, was sonst zu ständigen Fehlalarmen führen würde. Alle anderen Bänder (TG, TB, B, I, R, …) werden in der CSV gespeichert und in der Lichtkurve angezeigt, aber nicht für Alarme verwendet.
  - Nicht-Detektionen (AAVSO `<mag`-Obergrenzen, z. B. ein Beobachter mit flachem Teleskop, der `<4,9` meldet) werden eingelesen, aber von der Schwellenwertauswertung ausgeschlossen – nur bestätigte Messungen lösen Alarme aus.
- Zwei Stufen: `--warn-mag 8.0` (auffällig) und `--erupt-mag 6.0` (Ausbruch wahrscheinlich). Alarm nur bei Eskalation; `tcrb_state.json` verhindert doppelte Benachrichtigungen.
- Alarmierung über macOS-Benachrichtigung (`osascript`) und optional über Signal.

## Verwendung

```bash
python3 tcrb_monitor.py             # normaler Lauf
python3 tcrb_monitor.py --dry-run   # nur abrufen und anzeigen, keine Schreibvorgänge
python3 tcrb_monitor.py --test-alert  # Testnachricht auf allen aktiven Kanälen senden
```

## Einrichtung

Skript und Plist an einem festen Ort ablegen und alle `/Users/USERNAME/Scripts/tcrb`-Pfade in der Plist an die eigene Konfiguration anpassen. `which python3` zeigt den korrekten Interpreter-Pfad – `/usr/bin/python3` ist Apples System-Python und ausreichend, da das Skript nur die Standardbibliothek verwendet.

```bash
mkdir -p ~/Scripts/tcrb
cp tcrb_monitor.py ~/Scripts/tcrb/
cp tcrb_monitor_config.py ~/Scripts/tcrb/  # Geheimnisse; launchd lädt aus WorkingDirectory
cp de.agorion.tcrb.plist ~/Library/LaunchAgents/

# Syntax prüfen (keine Ausgabe = ok)
plutil -lint ~/Library/LaunchAgents/de.agorion.tcrb.plist

# Laden (modernes Syntax; "gui/$(id -u)" ist die eigene Login-Session)
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/de.agorion.tcrb.plist

# Sofort auslösen, ohne auf die nächste volle Stunde zu warten
launchctl kickstart -k gui/$(id -u)/de.agorion.tcrb
```

Prüfen ob es läuft:

```bash
launchctl print gui/$(id -u)/de.agorion.tcrb | grep -i state
cat ~/Scripts/tcrb/tcrb.log
```

Nach Änderungen an der Plist zuerst entladen, dann neu laden:

```bash
launchctl bootout gui/$(id -u)/de.agorion.tcrb
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/de.agorion.tcrb.plist
```

## Zwei Hinweise

- Die Plist löst stündlich zur vollen Stunde aus, rund um die Uhr – sinnvoll, weil Ausbruchsmeldungen weltweit zu jeder Zeit eintreffen und T CrB nach dem Höhepunkt schnell verblasst. Wenn das zu häufig ist, kann `StartCalendarInterval` als Array bestimmter Stunden angegeben werden (z. B. nur 6, 12, 18, 22 Uhr).
- Die macOS-Benachrichtigung über `osascript` läuft vom LaunchAgent innerhalb der GUI-Session und erscheint normalerweise ohne Probleme; beim ersten Start muss ggf. unter Systemeinstellungen → Mitteilungen eine Berechtigung erteilt werden. Falls Benachrichtigungen unzuverlässig wirken, ist Signal der robustere Kanal – jede Stunde zählt bei einem Ausbruch.

## Signal-Alarme

`tcrb_monitor_config.py` ausfüllen (Vorlage: `tcrb_monitor_config.sample.py`):

- `SIGNAL_CLI` – Pfad prüfen mit `which signal-cli` (Apple Silicon meist `/opt/homebrew/bin/signal-cli`)
- `SIGNAL_ACCOUNT` – die verknüpfte Telefonnummer
- Entweder `SIGNAL_GROUP_ID` (hat Vorrang) oder `SIGNAL_RECIPIENTS`
- `SIGNAL_ENABLED` ist im Skript-Header gesetzt und standardmäßig `True`; falls `tcrb_monitor_config.py` fehlt, deaktiviert sich Signal automatisch.

Noch zwei Dinge: Der signal-cli-Pfad muss absolut sein, da launchd nur ein minimales PATH bereitstellt. Und signal-cli speichert seinen Zustand in `~/.local/share/signal-cli` – da der LaunchAgent unter dem eigenen Benutzerkonto läuft, funktioniert dies ohne zusätzliche Konfiguration.

## signal-cli einrichten

```bash
brew install signal-cli
signal-cli link -n "TCrB-Monitor"          # zeigt einen QR-Code; im Handy scannen unter Einstellungen → Verknüpfte Geräte
signal-cli -u +49DEINENUM receive         # einmalig ausführen, um Kontakte und Gruppen zu laden
signal-cli -u +49DEINENUM listGroups      # gibt die Base64-Gruppen-ID zurück
```

Das Verknüpfen (statt Registrieren) ist der einfachere Weg: Das Handy bleibt primäres Gerät, der Mac wird ein sekundäres verknüpftes Gerät.

## Testen

```bash
python3 tcrb_monitor.py --test-alert
```

Sendet eine klar markierte Testnachricht auf allen aktiven Kanälen und beendet sich, ohne den Zustand zu ändern – ideal zum Überprüfen der Signal-Zustellung vor dem produktiven Betrieb.

## Lichtkurve plotten

Das Skript `plot_tcrb_csv.py` liest `tcrb_history.csv` und erzeugt `tcrb_lightcurve.png` – eine visuelle Lichtkurve mit Vis.- (gelb), V- (orange), TG- (grün) und TB- (blau) Bandmessungen. TG und TB sind One-Shot-Colour-(OSC-)Kamerabänder. B, I, R, CV, SU und TR sind ausgeschlossen (I/R: dauerhaft heller M-Riese; B/SU: systematisch versetzt). Fainter-than-Grenzen werden ebenfalls übersprungen. Die x-Achse zeigt UTC-Datum/Uhrzeit aus Julianischen Daten; die y-Achse ist wie üblich invertiert (heller oben). Der Titel zeigt den Datumsbereich der verfügbaren Daten automatisch.

```bash
.venv/bin/python plot_tcrb_csv.py

# Bestimmten AAVSO-Beobachter hervorheben: TG-Messungen als grüne Pentagons,
# TB-Messungen als hellblaue Pentagons (BSLA = Beobachtercode des Autors)
.venv/bin/python plot_tcrb_csv.py --observer BSLA
```

Benötigt `matplotlib`. Virtuelle Umgebung erstellen mit:

```bash
python3 -m venv .venv
.venv/bin/pip install matplotlib
```

Der Plotter liest den Produktions-CSV-Pfad aus `de.agorion.tcrb.plist` (`WorkingDirectory`), falls die Plist vorhanden ist – andernfalls aus dem Skriptverzeichnis. Wenn `asassn_history.csv` Daten enthält, wird die ASAS-SN g-Band-Serie automatisch als vierte Reihe überlagert (siehe unten); Titel und Legende werden entsprechend aktualisiert.

<img src="tcrb_lightcurve.sample.png">

## ASAS-SN-Referenzdaten

Der ASAS-SN-Fetcher (`asassn_fetch.py`) ist ein tägliches Begleitskript, das die T-CrB-Lichtkurve vom [ASAS-SN Sky Patrol](https://asas-sn.ifa.hawaii.edu/skypatrol/) abruft und neue Beobachtungen an `asassn_history.csv` anhängt. Die CSV verwendet dasselbe Spaltenlayout wie `tcrb_history.csv`, sodass `plot_tcrb_csv.py` beide Reihen zum Vergleich überlagern kann.

ASAS-SN liefert instrumental kalibrierte g-Band-Photometrie unabhängig von den visuellen Beobachtern der AAVSO – nützlich als Querprüfung, aber **nicht** für Alarme verwendet. Hinweis: Die ASAS-SN-Standard-Aperturfotometrie sättigt nahe der Helligkeit von T CrB beim Ausbruchsmaximum; die AAVSO-Vis.-/V-Daten bleiben die primäre Alarmquelle.

```bash
.venv/bin/python asassn_fetch.py             # abrufen und anhängen (ab 2026-06-14 standardmäßig)
.venv/bin/python asassn_fetch.py --dry-run   # abrufen und ausgeben, keine Schreibvorgänge
.venv/bin/python asassn_fetch.py --start-date 2026-01-01  # Startdatum überschreiben
.venv/bin/python asassn_fetch.py --start-date ''          # gesamtes Archiv abrufen
```

Benötigt `skypatrol` (und dessen Abhängigkeiten). Alles installieren mit:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

> **Aktuelles Problem (Stand Juni 2026):** ASAS-SN verarbeitet seit dem 22. Mai 2025 seine Photometriedatenbank neu und hat noch keine Beobachtungen für 2026 veröffentlicht. Der tägliche Lauf erzeugt lautlos 0 neue Zeilen, bis die Pipeline aufgeholt hat. Kein Handlungsbedarf – das Skript behandelt dies problemlos.

## Differenzielle Photometrie (eigene Aufnahmen)

Der primäre Photometrie-Workflow verwendet das [pi-aavso-photometry](https://github.com/beschne/pi-aavso-photometry)-PixInsight-Skript: einen plattengelösten Stack laden, Vergleichssterne und Zielobjekt anklicken, den AAVSO-Bericht direkt aus PixInsight exportieren. Kein separater Skriptschritt nötig.

Die Python-Skripte in [`photometry/`](photometry/README.md) sind Legacy-Alternativen, die als Querprüfung erhalten bleiben (Details in diesem README).

## Aufsuchkarte

Siehe [docs/FINDER_CHART.md](docs/FINDER_CHART.md) – AAVSO-Karte X42597QE (1° Sichtfeld, V-mag-Grenze 14,50) mit V-Magnitudenangaben für Vergleichssterne.

## Links

- [T CrB aktuell – TheSkyLive](https://theskylive.com/sky/stars/hr-5958-star) — aktuelle Helligkeit und Informationen zu T Coronae Borealis
- [AAVSO Photometry Database Search](https://apps.aavso.org/v2/data/search/photometry/) — Rohdaten aller AAVSO-Beobachtungen durchsuchen
- [Meine AAVSO-Beobachtungen (BSLA)](https://apps.aavso.org/v2/data/search/user/?observer=BSLA) — eigene eingereichte Beobachtungen, einschließlich der mit den Skripten in [`photometry/`](photometry/README.md) erzeugten. Erfordert einen kostenlosen AAVSO-Account (eingeloggt).
- [pi-aavso-photometry](https://github.com/beschne/pi-aavso-photometry) — PixInsight-Skript für Differenzphotometrie und direkten AAVSO-Berichtsexport aus plattengelösten Stacks.
