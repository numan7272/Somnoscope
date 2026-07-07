# Feature B — Fitbit Air über die Google Health API

> ⚠️ **Bewusste Ausnahme von Kernprinzip 1** („KEINE Cloud-API-Calls für
> Bio-Signale"). Die BLE-Payload des Fitbit Air ist Ende-zu-Ende verschlüsselt
> und **nur über Googles Cloud** entschlüsselbar — ein lokaler BLE-Read (wie
> beim geplanten `fitbit_ble`) ist technisch unmöglich. Wer die Air-Daten will,
> akzeptiert damit **einen** Cloud-Hop. Deshalb ist dieser Adapter **opt-in** und
> standardmäßig `enabled: false`.

## Warum nicht direkt per BLE?

Der Fitbit Air (Google, 2026) verschlüsselt seine BLE-Kommunikation
(LibTomCrypt, AES/XTEA; Schlüssel beim Pairing ausgehandelt). Passives Sniffen
liefert nur opake Blobs. Der einzige realistische Weg zu den eigenen Daten:

```
Fitbit Air → BLE (verschlüsselt) → Google-Health-App → Google Cloud → Google Health API → Somnoscope
```

## Kostet das etwas?

Nein. Der **Lesezugriff auf die eigenen Daten über die Google Health API ist
kostenlos** — kein Google-Health-Premium-Abo nötig (Premium sperrt nur den
KI-Coach, nicht die Rohdaten). Nicht zu verwechseln mit der kostenpflichtigen
Enterprise-„Cloud Healthcare API".

## Einrichtung

Der Adapter delegiert OAuth **und** Datenabruf an das offizielle CLI
[`ghealth`](https://github.com/Google-Health-API/google-health-cli) — so landet
kein OAuth-Code und kein Token im Repo (Tokens liegen unter `~/.config/ghealth/`).

1. **CLI installieren** (Go):
   ```bash
   git clone https://github.com/Google-Health-API/google-health-cli
   cd google-health-cli && go build -o ghealth .
   # ghealth ins PATH legen
   ```
2. **Einmalig authentifizieren:**
   ```bash
   ghealth setup   # Wizard: eigenes GCP-Projekt + Desktop-OAuth-Client anlegen
   ```
   Benötigte Scopes (readonly, „Restricted"):
   - `https://www.googleapis.com/auth/googlehealth.sleep.readonly`
   - `https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly`
3. **In `config.yaml` aktivieren:**
   ```yaml
   wearable:
     enabled: true
     adapters:
       - type: "fitbit_gh_api"
         enabled: true          # <— einschalten
         ghealth_bin: "ghealth"
         metrics: ["sleep", "heart-rate", "heart-rate-variability", "oxygen-saturation"]
         poll_interval_s: 1800
         lookback_days: 2
   ```

## ⚠️ Wichtigster Stolperstein: 7-Tage-Refresh-Token

Solange dein OAuth-Consent-Screen im Status **„Testing"** steht, verfällt das
Refresh-Token **nach 7 Tagen** → ein unbeaufsichtigter Poller stirbt wöchentlich.
Optionen:

- **Testing + wöchentliche Re-Auth** (einfachster Weg): `ghealth setup` wöchentlich erneut.
- **Production-Publish als Einzelnutzer**: entfernt das 7-Tage-Limit, kann aber
  das Restricted-Scope-Review triggern. Beim Setup empirisch klären.

## Datentyp-Mapping (Google Health → Somnoscope)

| Google Health (kebab)        | Somnoscope-Metrik | Einheit |
|------------------------------|-------------------|---------|
| `sleep` (`--detail`)         | `sleep_stage`     | –       |
| `heart-rate`                 | `heart_rate`      | bpm     |
| `heart-rate-variability`     | `hrv`             | ms      |
| `oxygen-saturation`          | `spo2`            | %       |
| `core-body-temperature`      | `skin_temp`       | °C      |

Der Adapter (`adapters/fitbit_gh_api.py`) parst die `ghealth`-JSON-Ausgabe
defensiv (Feldnamen des jungen Health-API-Schemas können sich ändern; das
Original-Payload bleibt in `WearableReading.raw` erhalten).
