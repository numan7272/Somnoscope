# ============================================================================
# Somnoscope — Container-Image (Web-Dashboard + Tracker aus EINEM Image)
# ============================================================================
# Design-Entscheidungen:
#   * python:3.12-slim — kleines Debian-Basis-Image; numpy/scipy/mne/yasa
#     liefern für linux/amd64+arm64 fertige Wheels, daher ist KEIN
#     build-essential nötig (spart >300 MB und Angriffsfläche).
#   * Layer-Reihenfolge: erst requirements.txt installieren, dann den Code
#     kopieren — Code-Änderungen invalidieren so nicht den teuren
#     pip-install-Layer (Docker-Build-Cache).
#   * Non-Root: Der Prozess läuft als unprivilegierter User `somnoscope`.
#     Nur /app/data und /app/logs sind für ihn beschreibbar, der Code
#     gehört root und bleibt read-only (Defense in Depth).
#   * Privacy First bleibt gewahrt: Das Image telefoniert nirgendwohin;
#     alle Daten liegen im Volume unter /app/data.
# ============================================================================

FROM python:3.12-slim

# --- Python-Laufzeitverhalten ------------------------------------------------
# PYTHONDONTWRITEBYTECODE: keine .pyc im Image-Layer (read-only Code-Dir).
# PYTHONUNBUFFERED: Logs sofort nach stdout/stderr -> `docker logs` in Echtzeit.
# PIP_NO_CACHE_DIR: kein pip-Download-Cache im Layer (kleineres Image).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# --- Abhängigkeiten (eigener Cache-Layer) -------------------------------------
# Nur requirements.txt kopieren, damit dieser Layer stabil bleibt, solange
# sich die Abhängigkeiten nicht ändern.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# --- Anwendungscode ------------------------------------------------------------
# .dockerignore hält data/, logs/, .git, tests/ etc. aus dem Kontext heraus.
COPY . .

# --- Entrypoint ----------------------------------------------------------------
# Nach /usr/local/bin (liegt im PATH); chmod explizit, weil auf Windows-Hosts
# das Executable-Bit beim COPY nicht zuverlässig ankommt.
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod 0755 /usr/local/bin/docker-entrypoint.sh

# --- Non-Root-User -------------------------------------------------------------
# Feste UID/GID (1000) macht Volume-Berechtigungen auf dem Host vorhersagbar.
# Nur die Laufzeit-Verzeichnisse gehören dem User; der Code bleibt root/read-only.
RUN groupadd --gid 1000 somnoscope \
    && useradd --uid 1000 --gid somnoscope --create-home somnoscope \
    && mkdir -p /app/data /app/logs \
    && chown -R somnoscope:somnoscope /app/data /app/logs
USER somnoscope

# --- Laufzeit-Kontrakt ----------------------------------------------------------
EXPOSE 8000

# Healthcheck über die Python-Stdlib statt curl (curl ist in slim nicht
# installiert — so sparen wir uns ein zusätzliches Paket). Prüft die
# Dashboard-Root-Route (`GET /` in webui/app.py).
# Hinweis: Gilt für den "web"-Betrieb; der Tracker-Service überschreibt den
# Healthcheck in docker-compose.yml (er lauscht auf keinem Port).
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/', timeout=4)" || exit 1

# Default: Web-Dashboard. Der Tracker läuft aus demselben Image via
# `command: tracker` (siehe docker-entrypoint.sh / docker-compose.yml).
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["web"]
