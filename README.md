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
| Benutzer / Passwort Weboberfläche | nur CP850/CP950: dieselben Zugangsdaten wie im Browser, nur für die Atmos-Anzeige. Leer lassen = keine Atmos-Anzeige |

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

## Entitäten

| Entität | CP750 | CP850 / CP950(A) |
|---------|-------|------------------|
| **Auswahl „Eingang“ / „Makro“** | `cp750.sys.input_mode` (Digital 1–4, Multi-Ch Analog, NonSync, Mic) | `sys.macro_preset` / `sys.macro_name` |
| **Zahl „Lautstärke“** | `cp750.sys.fader`, 0.0–10.0 (7.0 = Referenzpegel) | `sys.fader` |
| **Schalter „Mute“** | `cp750.sys.mute` | `sys.mute` |
| **Buttons „Lauter“ / „Leiser“** | `cp750.ctrl.fader_delta ±1` (= ±0,1) | `ctrl.fader_delta ±1` |
| **Binärsensor „Dolby Atmos“** | – | an, solange Atmos **wirklich gerendert** wird |
| Binärsensor „Verbunden“ | Diagnose | Diagnose |

Die Buttons ändern die Lautstärke relativ – ein gleichzeitiger Dreh am Knopf
des Geräts wird damit nicht überschrieben. Nach jedem Befehl liest die
Integration den Zustand sofort neu ein.

### Makros (CP850/CP950)

Über ASCII liefert der Prozessor nur Nummer und Name des **aktiven** Makros,
keine Liste aller Makros. Die Integration merkt sich deshalb jedes Makro, das
einmal aktiv war (bleibt über Neustarts erhalten). Damit von Anfang an alle
Makros in der Auswahl stehen, in den Optionen eintragen – ein Makro pro Zeile,
Nummer wie in der Makro-Reihenfolge des Geräts:

```
1=5.1 + Dolby Atmos
2=7.1 + Dolby Atmos
6=Pausenmusik
```

### Dolby Atmos

Der Sensor zeigt nicht, ob ein Atmos-*Makro* aktiv ist, sondern ob der
Prozessor tatsächlich Atmos rendert. Fällt er z. B. wegen eines Problems auf
dem AES-Strang auf 5.1/7.1 zurück, geht der Sensor aus, obwohl das Makro
weiter „… + Dolby Atmos“ heißt.

Quelle ist dieselbe Schnittstelle, die die Weboberfläche des Prozessors für
ihre Statusseite nutzt (`/prototype/TroutMockData?get_updates`, nur lesend,
HTTP Basic Auth). Solange Atmos läuft, meldet das Gerät laufend
`AtmosContentEvent` mit `status: 1`; endet Atmos, kommt einmal `status: 2`.
Bleiben die Meldungen 20 s aus, gilt Atmos ebenfalls als beendet.

Attribute des Sensors: `status_code`, `reversion_reason` (Rückfallgrund, wie
vom Gerät gemeldet), `frame_rate`, `uuid`, `encrypted`.

Ist die Weboberfläche nicht erreichbar oder das Passwort falsch, wird nur der
Atmos-Sensor „nicht verfügbar“; Lautstärke, Mute und Makro laufen weiter.

## Aktion `cinema_processor.send_command`

Sendet eine ASCII-Zeile an den Steuerport und gibt alle Antworten zurück. Im
Modus „Nur lesen“ sind nur Abfragen (`… ?`), `help` und `status` erlaubt.

```yaml
action: cinema_processor.send_command
data:
  device_id: <Gerät>
  command: "help"        # CP850/CP950: listet alle Parameter
response_variable: antwort
```

## Schonender Umgang mit dem Gerät

Der CP850 sperrt bekanntermaßen seinen Steuerport, wenn viele TCP-Verbindungen
schnell hintereinander aufgebaut werden (Ton läuft weiter, Steuerung erst nach
Neustart wieder). Die Integration deshalb:

- hält **eine** dauerhafte Verbindung pro Prozessor und meldet sich beim
  Entladen mit `exit` ab (der CP750 erlaubt max. 20 Sitzungen),
- wartet mindestens 0,15 s zwischen zwei Befehlen,
- baut zum selben Gerät höchstens alle 3 s eine Verbindung auf und nach einem
  Verbindungsabbruch frühestens nach 10 s wieder.

## Noch am Gerät zu prüfen

| Punkt | Stand |
|-------|-------|
| CP750 ASCII-Befehle | laut Handbuch, noch nicht am Gerät getestet |
| CP850/CP950 Lautstärke-Skala (`sys.fader 70` = 7.0?) | angenommen wie CP750; Werte mit Komma (`7.0`) werden ebenfalls verstanden |
| CP850/CP950 Makro wechseln per `sys.macro_preset <Nr>` | angenommen, noch nicht getestet |
| Atmos-Sensor CP850 | Ereignisse am Gerät mitgeschnitten (Atmos an/aus) |
| Atmos-Sensor CP950A | gleiche Schnittstelle gesehen, Atmos-Durchlauf noch nicht |
| Stört das Mitlesen von `get_updates` eine offene Weboberfläche? | noch offen – falls die Statusseite im Browser hakt, Zugangsdaten leer lassen |

Hilfreich für die Prüfung: Aktion `send_command` mit `help` bzw. `status`
(geht auch im Modus „Nur lesen“).

## Tests (ohne Home Assistant)

```
python3 tests/test_dolby.py                 # Simulator-Tests
python3 tests/fake_dolby.py cp850 61408 8080  # Simulator zum Ausprobieren (Web: admin/secret)
```

Dieses Projekt ist nicht mit Dolby Laboratories verbunden.
