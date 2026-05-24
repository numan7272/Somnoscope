<!--
Vielen Dank für deinen Pull Request!
Bitte fülle die folgenden Abschnitte aus. Lösche Punkte, die nicht zutreffen.
-->

## 🔗 Verknüpfter Issue

Closes #<nummer>

> ⚠️ Bitte das verlinkte Issue **nicht beim PR-Open** schließen — Issues werden
> nach dem Merge mit einer Root-Cause-Notiz manuell geschlossen.

## ✏️ Was wurde geändert?
<!-- Kurzer technischer Überblick. Welche Dateien/Module sind betroffen? -->

## 🤔 Warum?
<!-- Was war das Problem, das gelöst werden sollte? Welche Designentscheidungen
     standen zur Auswahl, und warum wurde dieser Weg gewählt? -->

## 🧪 Wie wurde getestet?
<!-- Manuelle Tests, automatisierte Tests, Logfile-Auszüge.
     Bei BLE/MQTT/Hardware-Themen: bitte konkret beschreiben (welches Gerät, welche Schritte). -->

## 📦 Art der Änderung
- [ ] Bug-Fix (`fix/...`)
- [ ] Neues Feature / Modul (`feat/...`)
- [ ] Refactoring ohne Verhaltensänderung (`refac/...`)
- [ ] Dokumentation (`docs/...`)
- [ ] Wartung / Dependencies / CI (`chore/...`)

## ✅ Checkliste
- [ ] Branch-Name folgt dem Schema `<typ>/issue-<nr>-<kurztitel>`
- [ ] `python main.py` läuft lokal ohne Fehler
- [ ] Neue/geänderte Funktionen haben **deutsche Docstrings** (Args, Returns, Seiteneffekte)
- [ ] Type-Hints konsequent (`str | None` statt `Optional[str]`)
- [ ] Keine Cloud-Calls für Gesundheitsdaten eingeführt
- [ ] Keine echten Tokens / EDF-Daten / Secrets im Diff
- [ ] `config.yaml`-Schema angepasst, falls neue Module/Felder
- [ ] CLAUDE.md / README.md aktualisiert, falls Architektur betroffen

## 📎 Screenshots / Logs (optional)
<!-- Bei UI- oder Logging-Änderungen: ein Vorher/Nachher hilft enorm. -->
