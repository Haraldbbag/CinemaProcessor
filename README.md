# Cinema Processor für Home Assistant

Custom Integration zur Steuerung von Dolby-Kinoprozessoren über das Netzwerk –
Einrichtung komplett über die Oberfläche, beliebig viele Prozessoren pro
Home-Assistant-Instanz.

| Modell | Steuerung | Atmos-Anzeige |
|--------|-----------|---------------|
| **Dolby CP750** | ASCII, Port 61408, Befehle mit `cp750.`-Präfix | – (kann kein Atmos) |
| **Dolby CP850** | ASCII, Port 61408, Befehle ohne Präfix | ja, über die Weboberfläche |
| **Dolby CP950 / CP950A** | ASCII, Port 61408, Befehle ohne Präfix | ja (nur CP950A), über die Weboberfläche |

## Installation

### Über HACS

1. In jeder HA-Instanz: **HACS → ⋮ → Benutzerdefinierte Repositories** →
   `https://github.com/Haraldbbag/CinemaProcessor`, Typ *Integration* →
   herunterladen → HA neu starten.
2. Updates: `version` im Manifest erhöhen – HACS bietet das Update in allen Instanzen an.

### Manuell

Ordner `custom_components/cinema_processor` nach `/config/custom_components/`
kopieren und HA neu starten.

## Einrichtung

**Einstellungen → Geräte & Dienste → Integration hinzufügen → Cinema Processor**,
dann das Modell wählen.

| Feld | Bedeutung |
|------|-----------|
| Host | IP-Adresse des Prozessors |
| Port | ASCII-Steuerport, Standard 61408 |
| Name | z. B. „Kino 1“ – Gerätename und Präfix der Entitäten |
| Benutzer / Passwort Weboberfläche | nur CP850/CP950: dieselben Zugangsdaten wie im Browser (vorbelegt mit der Dolby-Werkseinstellung admin/admin), nur für die Atmos-Anzeige. Benutzer leeren = keine Atmos-Anzeige |

Pro Prozessor erneut hinzufügen. IP-Wechsel oder neues Passwort: **⋮ → Neu konfigurieren**.

**Jeder Prozessor wird zunächst mit „Nur lesen“ eingerichtet.** Die
Integration zeigt dann alles an, schickt aber keine Schaltbefehle. Nach dem
ersten erfolgreichen Test in den Optionen ausschalten.

### Optionen (⋮ → Optionen)

| Option | Standard | Wirkung |
|--------|----------|---------|
| Abfrageintervall | 5 s | Polling von Lautstärke, Mute, Eingang/Makro, Atmos |
| Nur lesen | an | Blockiert alle Schaltbefehle |
| Makronamen | leer | nur CP850/CP950, siehe unten |
| Eingangsnamen | leer | nur CP750, siehe unten |

## Entitäten

| Entität | CP750 | CP850 / CP950(A) |
|---------|-------|------------------|
| **Auswahl „Eingang“ / „Makro“** | `cp750.sys.input_mode` (Digital 1–4, Multi-Ch Analog, NonSync, Mic) | `sys.macro_preset` / `sys.macro_name` |
| **Zahl „Lautstärke“** | `cp750.sys.fader`, 0.0–10.0 (7.0 = Referenzpegel) | `sys.fader` |
| **Schalter „Mute“** | `cp750.sys.mute` | `sys.mute` |
| **Buttons „Lauter“ / „Leiser“** (±0,1) | `cp750.ctrl.fader_delta ±1` (= ±0,1) | `ctrl.fader_delta ±1` |
| **Binärsensor „Dolby Atmos“** | – | an, solange Atmos **wirklich gerendert** wird |
| **Schalter „Atmos ignorieren (PCM)“** | – | wie Button „ignore Dolby Atmos“ der Weboberfläche: an = PCM-Fallback wird gespielt |
| Binärsensor „Verbunden“ | Diagnose | Diagnose |

Die Buttons ändern die Lautstärke relativ – ein gleichzeitiger Dreh am Knopf
des Geräts wird damit nicht überschrieben. Nach jedem Befehl liest die
Integration den Zustand sofort neu ein.

### Eingänge (CP750)

Statt „Digital 1“ usw. lassen sich eigene Namen vergeben: **⋮ → Optionen →
Eingangsnamen**, ein Eingang pro Zeile als `Eingang=Name`:

```
dig_1=Kinoserver
dig_2=Blu-ray
non_sync=Pausenmusik
mic=Mikro
```

Eingänge: `dig_1` bis `dig_4`, `analog` (Multi-Ch Analog), `non_sync`, `mic`
(die Standardnamen wie `Digital 1` gehen auch). **Nur eingetragene Eingänge
stehen dann in der Auswahl**; ist der CP750 gerade auf einem nicht
eingetragenen Eingang, wird dieser trotzdem angezeigt. Leer = alle sieben mit
Standardnamen.

### Makros (CP850/CP950)

Die Auswahl enthält die Makros, die im Makro-Editor des Geräts auf einer
**Taste (front panel button 1–8)** liegen – das sind die, die
`sys.macro_preset` per ASCII schalten kann. Makros ohne Taste erscheinen nicht.

- **Mit Zugangsdaten der Weboberfläche** liest die Integration diese Liste
  selbst vom Gerät (`MacroConfigurationEvent`, alle 10 Minuten neu).
  Umbenennen oder neu belegen am Gerät kommt also von allein an.
- **Ohne Zugangsdaten** liefert ASCII nur das **aktive** Makro. Die Integration
  merkt sich dann jedes Makro, das einmal aktiv war. Damit von Anfang an alle in
  der Auswahl stehen, in den Optionen eintragen – ein Makro pro Zeile als
  `Tastennummer=Name`:

```
1=7.1 Dolby Surround + Dolby Atmos
2=5.1 + Dolby Atmos
3=BluRay mit Upmix
```

Ein Eintrag in den Optionen überschreibt den Namen vom Gerät.

### Dolby Atmos

Der Sensor zeigt nicht, ob ein Atmos-*Makro* aktiv ist, sondern ob der
Prozessor tatsächlich Atmos rendert. Fällt er z. B. wegen eines Problems auf
dem AES-Strang auf 5.1/7.1 zurück, geht der Sensor aus, obwohl das Makro
weiter „… + Dolby Atmos“ heißt.

Quelle ist dieselbe Schnittstelle, die die Weboberfläche des Prozessors für
ihre Statusseite nutzt: Die Integration meldet sich wie der Browser an
(`TroutMockData?login`, Formular mit Benutzer/Passwort, Antwort enthält eine
`sessionId`) und liest dann nur `TroutMockData?get_updates` (Header
`SessionId`/`ClientId`). Läuft die Sitzung ab (z. B. Neustart des Prozessors),
meldet sie sich selbst neu an. Solange Atmos läuft, meldet das Gerät laufend
`AtmosContentEvent` mit `status: 1`; endet Atmos, kommt einmal `status: 2`.
Bleiben die Meldungen 20 s aus, gilt Atmos ebenfalls als beendet.

Attribute des Sensors: `status_code`, `reversion_reason` (Rückfallgrund, wie
vom Gerät gemeldet), `frame_rate`, `uuid`, `encrypted`, `pcm_forced`
(Schalter „Atmos ignorieren“ an) und `last_change` (Zeitpunkt des letzten
Wechsels Atmos ↔ PCM, auf die Millisekunde).

Ist die Weboberfläche nicht erreichbar oder das Passwort falsch, werden nur
Atmos-Sensor und Atmos-Schalter „nicht verfügbar“; Lautstärke, Mute und Makro
laufen weiter.

### Atmos ignorieren (QC: PCM-Fallback gegen Atmos)

Der Schalter macht dasselbe wie der Button „ignore Dolby Atmos“ auf der
Statusseite der Weboberfläche: Der Prozessor spielt dann die PCM-Spur vom
AES-Eingang, also die Fallback-Ebene für Atmos. Gedacht für die
Qualitätskontrolle, ob PCM- und Atmos-Ton synchron sind – beim Umschalten
sollte nichts zu hören sein.

- Gesendet wird wie von der Weboberfläche
  `POST TroutMockData?send_message` mit
  `[{"id":"ForcePcmControlEvent_1066", "forcePCM":true}]` (bzw. `false`).
- Der Zustand des Schalters kommt vom Gerät (`ForcePcmControlEvent`), nicht
  aus einer Annahme. Am CP850 gemessen: PCM ca. 0,3 s, zurück zu Atmos ca.
  0,1 s nach dem Befehl.
- Der Prozessor schaltet nach **Makrowechsel oder Neustart** von selbst wieder
  auf Atmos; der Schalter folgt. Nach einem Neustart von Home Assistant ist der
  Zustand bis zum nächsten Wechsel unbekannt – sobald Atmos gerendert wird,
  steht er sicher auf „aus“.
- Die Weboberfläche fragt vor dem Umschalten nach einer Anmeldung; die
  Integration nutzt dafür die eingetragenen Zugangsdaten.

## Aktion `cinema_processor.send_command`

Sendet eine ASCII-Zeile an den Steuerport und gibt alle Antworten zurück. Im
Modus „Nur lesen“ sind nur Abfragen (`… ?`), `help` und `status` erlaubt.

```yaml
action: cinema_processor.send_command
data:
  device_id: <Gerät>
  command: "sys.macro_preset ?"   # -> ["sys.macro_preset 2"]
response_variable: antwort
```

Der CP850 beantwortet `help` und `status` nicht (leere Antwort); der CP750
listet damit laut Handbuch alle Parameter.

## Schonender Umgang mit dem Gerät

Der CP850 sperrt bekanntermaßen seinen Steuerport, wenn viele TCP-Verbindungen
schnell hintereinander aufgebaut werden (Ton läuft weiter, Steuerung erst nach
Neustart wieder). Die Integration deshalb:

- hält **eine** dauerhafte Verbindung pro Prozessor und meldet sich beim
  Entladen mit `exit` ab (der CP750 erlaubt max. 20 Sitzungen),
- wartet mindestens 0,15 s zwischen zwei Befehlen,
- baut zum selben Gerät höchstens alle 3 s eine Verbindung auf und nach einem
  Verbindungsabbruch frühestens nach 10 s wieder.

## Getestet

| Punkt | Stand |
|-------|-------|
| CP850: Lautstärke lesen/setzen, ±0,1, Mute, Makro lesen/wechseln (`sys.macro_preset <Nr>`) | am Gerät getestet (HA Mathilde, 13.10.2026); `sys.fader 40` = 4.0 |
| CP850: Atmos-Ereignisse der Weboberfläche (an/aus) | am Gerät mitgeschnitten |
| CP850: „ignore Dolby Atmos“ (`ForcePcmControlEvent`) | Befehl und Rückmeldung am Gerät mitgeschnitten (19.10.2026) |
| CP950/CP950A | gleiche ASCII-Befehle und gleiche Weboberfläche; Atmos-Durchlauf noch nicht |
| CP750 | laut Handbuch, noch nicht am Gerät getestet |
| Stört die zusätzliche Web-Anmeldung eine offene Weboberfläche im Browser? | noch offen – falls der Browser abgemeldet wird oder die Statusseite hakt, Benutzer leeren |

## Tests (ohne Home Assistant)

```
python3 tests/test_dolby.py                 # Simulator-Tests
python3 tests/fake_dolby.py cp850 61408 8080  # Simulator zum Ausprobieren (Web: admin/secret)
```

Dieses Projekt ist nicht mit Dolby Laboratories verbunden.
