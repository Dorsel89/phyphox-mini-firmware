# phyphox:mini Testskripte

Automatische Tests für die BLE-Schnittstelle eines phyphox:mini. Jeder Sensor
hat ein eigenes Skript, `run_all.py` fasst alles in einem Durchlauf zusammen.

Neben Bestanden/Durchgefallen schreibt jeder Test **benannte Messwerte** in eine
fortlaufende Sammeldatei. Dadurch lässt sich dasselbe Board in einem Jahr erneut
messen und vergleichen — etwa um zu sehen, ob der Gyro-Nullpunkt gewandert ist
oder das Rauschen zugenommen hat.

## Voraussetzungen

```
pip install bleak
```

Sonst nichts. Der HTML-Report kommt ohne Abhängigkeiten und ohne Internet aus.

## Schnellstart

```bash
# alle Tests gegen ein Board, ausgewaehlt ueber den Namen (sucht "phyphox:mini A19")
python run_all.py --board A19

# oder ueber die BLE-Adresse
python run_all.py --address E8:C6:21:E2:29:78

# kurze Fassung, etwa 3 Minuten statt 10
python run_all.py --board A19 --fast

# nur einzelne Tests
python run_all.py --board A19 --only lsm6dsr,datalog

# HTML-Auswertung über alle bisher gemessenen Boards und Zeitpunkte
python make_report.py
```

Jedes Skript läuft auch allein:

```bash
python test_lsm6dsr.py --board A19
python test_datalog.py --board A19 --log-seconds 300 --interval 2
```

## Was geprüft wird

| Skript | Inhalt |
|---|---|
| `test_device_info.py` | Gerätename, MTU, alle 13 Characteristics vorhanden, DIS-Strings, Ladezustand |
| `test_bmp581.py` | Druck und Temperatur plausibel, **Oversampling ändert die Paketgröße** wie dokumentiert, **IIR-Filter senkt das Rauschen**, Abschalten wirkt |
| `test_hdc1080.py` | Temperatur und Feuchte plausibel, **Intervall-Einstellung schlägt auf die gemessene Abtastrate durch**, Erkennung eines nicht antwortenden Sensors |
| `test_stcc4.py` | CO2 plausibel und nicht eingefroren, Intervall-Einstellung, Erkennung durchgehender Nullwerte |
| `test_lsm6dsr.py` | Paketlänge folgt `event_size` (float und int16), alle Messbereiche, Datenraten, Enable-Bits, **Referenzmessung von Rauschen und Offset** |
| `test_datalog.py` | Konfiguration schreiben und zurücklesen, Aufzeichnen ohne Verbindung, vollständiger Abruf, Altersfilter, Wiederherstellung der alten Einstellung |

### Das Wichtigste zum Verständnis

**Ohne Start-Kommando kommen keine Daten.** Die Firmware verwirft jede
Notification, bis `0x01` auf die Run-Control-Characteristic `cddf0004`
geschrieben wurde. Das erledigt `MiniDevice.start_run()`; wer eigene Skripte
schreibt, darf es nicht vergessen.

**Aufzeichnung pausiert bei bestehender Verbindung.** Deshalb trennt
`test_datalog.py` die Verbindung, wartet und verbindet sich neu.

**Verbindungsparameter setzt der Client, nicht die Firmware.** Fünf Sekunden
nach dem Verbindungsaufbau fordert der Fob von sich aus Zephyrs Standardwerte an:
30–50 ms Intervall mit nur **420 ms** Supervision-Timeout. Unter Last laufen
damit die Sendepuffer über, und ein kurzer Funkaussetzer beendet die Verbindung.
Die Skripte fordern deshalb gleich nach dem Verbinden per `cddf1022` eigene
Werte an (Standard 30 ms / 2,5 s) und wiederholen das nach sechs Sekunden —
Zephyr schickt seine Werte auch dann, wenn der Client vorher etwas anderes
verlangt hat, und so kommen unsere garantiert als letzte an.

**Nicht unter 30 ms gehen.** Bei 15 ms Verbindungsintervall schlägt jede
HDC1080-Messung fehl, der Fob sendet dann −40 °C / 100 % (gemessen an A19:
15 von 15 Messungen bei 15 ms, keine einzige bei 30, 50 oder 100 ms).

```bash
python run_all.py --address ... --conn-params 30,30,0,2500   # Standard
python run_all.py --address ... --no-conn-params             # Firmware-Werte behalten
```

Die Firmware speichert alle vier Werte als einzelnes Byte: Intervalle in
1,25-ms-Schritten, den Timeout in 10-ms-Schritten, also höchstens 2550 ms.
Windows und iOS akzeptieren kein Intervall unter 15 ms.

**Einige Tests decken bekannte Firmware-Eigenheiten auf.** Sie schlagen dann
bewusst fehl und erklären im Detailtext, was los ist:

- Ist die Firmware älter als der Paketlängen-Fix, sendet sie im Float-Format
  immer 240 Byte, egal was `event_size` sagt. Nur `event_size=15` liefert dort
  brauchbare Daten.
- Ist die Firmware älter als der Skalierungs-Fix, sind im Float-Format die
  Beschleunigungswerte in den Bereichen 2 g, 4 g und 8 g um den Faktor 9,81 zu
  groß. Nur 16 g rechnet dort korrekt.
- Der Sensortakt weicht systematisch von der Nennrate ab, gemessen rund 8 %.

## Die Messwert-Sammeldatei

`results/measurements.jsonl`, eine Zeile je Test und Durchlauf, nur angehängt,
nie umgeschrieben. Jede Zeile enthält neben den Messwerten auch alle
Einzelprüfungen mit ihrem Ergebnis, damit der Report zeigen kann, was genau
fehlgeschlagen ist. Das bleibt lesbar, lässt sich zusammenführen und wächst über
Jahre problemlos.

```json
{"schema":1,"run_id":"2026-09-28T…","ts":"2026-09-28T…","board_id":"E8:C6:21:E2:29:78",
 "board_name":"A24","firmware":"1.1.0+0","test":"lsm6dsr",
 "counts":{"PASS":21,"FAIL":0,…},
 "metrics":{"lsm.gyr_bias_x_dps":{"value":0.42,"unit":"deg/s","label":"Gyro-Nullpunkt X"}, …},
 "checks":[{"section":"Datenraten","verdict":"PASS","title":"104 Hz gemessen","detail":"110.9 Hz, Faktor 1.066"}, …]}
```

`board_id` ist die BLE-Adresse. Die leitet sich aus dem FICR des nRF52832 ab und
bleibt über die Lebensdauer des Chips gleich — deshalb taugt sie als dauerhafte
Kennung, auch wenn der Anzeigename später geändert wird.

### Wichtige Messgrößen für den Langzeitvergleich

| Schlüssel | Bedeutung |
|---|---|
| `lsm.gyr_bias_{x,y,z}_dps` | Gyro-Nullpunkt in Ruhe — **der klassische Alterungsindikator** |
| `lsm.gyr_noise_{x,y,z}_dps` | Gyro-Rauschen (Standardabweichung) |
| `lsm.acc_noise_{x,y,z}_mg` | Beschleunigungsrauschen je Achse |
| `lsm.acc_offset_mg` | Abweichung des Betrags von 1 g |
| `lsm.odr_ratio` | gemessene Datenrate geteilt durch Nennrate |
| `bmp.pressure_noise_iir*_hpa` | Druckrauschen bei verschiedenen Filterstufen |
| `device.battery_pct` | Ladezustand |

Die Referenzmessung im LSM-Test läuft mit **fest verdrahteter Konfiguration**
(int16, 104 Hz, ±2 g, ±125 °/s, 8 s in Ruhe). Diese Werte stehen als Konstanten
oben in `test_lsm6dsr.py` und sollten nicht verändert werden — die gespeicherte
Historie ist nur vergleichbar, wenn alle Läufe dieselben Einstellungen benutzt
haben.

Damit die Messung aussagekräftig ist, muss das Board während des LSM-Tests
**ruhig und erschütterungsfrei** liegen.

## Der HTML-Report

`python make_report.py` erzeugt `results/report.html`, eine in sich geschlossene
Seite ohne externe Abhängigkeiten. Oben wählt man zwischen der Übersicht und
den einzelnen Boards.

- **Übersicht** — je Board eine Zeile mit dem Gesamtergebnis (bestanden,
  Warnungen, fehlgeschlagen) und einem Zeichen je Test. Darunter die Messwerte
  aller Boards im Vergleich, als Tabelle und als Balken. Gezeigt wird je Board
  und Test **nur der letzte Lauf**: Ein Board, das repariert und neu getestet
  wurde, erscheint mit seinem aktuellen Stand.
- **Board-Ansicht** — alle Prüfungen der letzten Läufe mit Detailtext
  (fehlgeschlagene Tests sind aufgeklappt), alle Messwerte mit dem Vergleich
  zur vorherigen Messung und, sobald das Board mehrmals gemessen wurde, der
  Verlauf über die Zeit — etwa „hat sich der Gyro-Nullpunkt seit letztem Jahr
  verschoben".

Die Seite wird nicht automatisch aktualisiert. Nach neuen Testläufen einfach
`python make_report.py` erneut aufrufen.

## Aufbau

```
tests/
  minitest/
    spec.py       UUIDs, Konfigurationsbytes, Frame-Layouts, Enums
    ble.py        Verbindung, Notifications, Messfenster, Frame-Dekodierung
    report.py     Prüfungen und Messwerte sammeln und ausgeben
    store.py      die fortlaufende JSONL-Sammeldatei
    runner.py     Rahmen, damit jedes Skript auch allein läuft
  test_*.py       je ein Sensor
  run_all.py      alles in einer Verbindung, ein gemeinsamer run_id
  make_report.py  HTML-Auswertung
  results/        Messwerte und erzeugter Report
```

`spec.py` ist die einzige Stelle, an der UUIDs und Byte-Layouts stehen. Ändert
sich die Firmware, reicht dort eine Anpassung. Inhaltlich entspricht die Datei
`../phyfob-interface.yaml`.

## Zustand nach einem Lauf

Die Skripte räumen hinter sich auf: Sensoren werden abgeschaltet, die
Datalog-Konfiguration wird auf den vorgefundenen Stand zurückgesetzt
(abschaltbar mit `--no-restore`). Die Forced Recalibration des STCC4 wird
**nicht** automatisch ausgeführt, weil sie die Sensorkalibrierung dauerhaft
verändert.
