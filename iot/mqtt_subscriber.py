"""
MQTT-Subscriber fuer ESP32-Klimadaten (CO2, Temperatur, Luftfeuchte).

Verbindet sich mit dem lokalen Mosquitto-Broker, abonniert die in
``config.yaml`` unter ``climate_sensors.topics`` konfigurierten Topics
(z.B. ``bedroom/co2``) und schreibt eingehende Werte in einen
:class:`iot.climate_buffer.ClimateBuffer`.

Graceful Degradation (Kernprinzip 2):
    * ``paho-mqtt`` ist eine *optionale* Abhaengigkeit — fehlt sie, loggt
      :meth:`MqttSubscriber.run` eine klare Warnung und kehrt zurueck,
      statt beim Import zu crashen.
    * Ist der Broker nicht erreichbar, wird gewarnt und mit Backoff neu
      versucht; die Schlafanalyse laeuft derweil ohne Klimadaten weiter.

Asyncio-Integration:
    ``paho-mqtt`` ist eine blockierende Bibliothek mit eigenem
    Netzwerk-Thread (``loop_start``). Der blockierende ``connect``-Aufruf
    laeuft daher via :func:`asyncio.to_thread`; die Message-Callbacks
    schreiben direkt in den thread-sicheren :class:`ClimateBuffer`.
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timezone
from typing import Any

from core.config_loader import ClimateSensorsConfig
from core.constants import TOPIC_SUFFIX_TO_METRIC
from iot.climate_buffer import ClimateBuffer

logger = logging.getLogger(__name__)

# -- Optionale Abhaengigkeit: paho-mqtt (Graceful Degradation) ----------------
try:
    import paho.mqtt.client as mqtt_client  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - haengt von der Umgebung ab
    mqtt_client = None

#: Wartezeit zwischen zwei Verbindungsversuchen (Sekunden).
DEFAULT_RETRY_INTERVAL_S = 30.0

#: Obergrenze fuer paho's internen Auto-Reconnect-Backoff (Sekunden).
_RECONNECT_MAX_DELAY_S = 60


def topic_to_metric(topic: str) -> str | None:
    """
    Uebersetzt ein MQTT-Topic in den vereinheitlichten Metrik-Namen.

    Massgeblich ist ausschliesslich das Segment nach dem letzten ``/``
    (z.B. ``bedroom/co2`` -> ``co2``); der Raum-Praefix ist egal.

    Args:
        topic: Volles MQTT-Topic, z.B. ``"bedroom/temperature"``.

    Returns:
        Metrik-Name (``"co2"`` / ``"temperature"`` / ``"humidity"``) oder
        ``None``, wenn das Topic keiner bekannten Metrik entspricht.
    """
    suffix = topic.rsplit("/", 1)[-1].strip().lower()
    return TOPIC_SUFFIX_TO_METRIC.get(suffix)


def parse_payload(payload: bytes | str) -> float | None:
    """
    Parst eine MQTT-Payload defensiv als float.

    Ungueltige Payloads (kein Zahlenformat, kaputtes Encoding, ``nan``/
    ``inf`` — die wuerden Durchschnitte still vergiften) fuehren zu
    ``None`` plus Debug-Log statt zu einer Exception.

    Args:
        payload: Rohe Payload als ``bytes`` oder ``str``.

    Returns:
        Der Messwert als (endlicher) float oder ``None`` bei ungueltiger
        Payload.
    """
    try:
        text = payload.decode("utf-8") if isinstance(payload, bytes) else payload
        value = float(text.strip())
    except (UnicodeDecodeError, ValueError, AttributeError):
        logger.debug("Klima-Payload verworfen (kein float): %r", payload)
        return None
    if not math.isfinite(value):
        logger.debug("Klima-Payload verworfen (nicht endlich): %r", payload)
        return None
    return value


def _is_failure(reason_code: Any) -> bool:
    """
    Prueft versionsuebergreifend, ob ein paho-Reason-Code einen Fehler meldet.

    paho-mqtt v2 liefert ``ReasonCode``-Objekte mit ``is_failure``-Property,
    v1 schlichte Integer (0 = Erfolg).

    Args:
        reason_code: Reason-Code aus einem paho-Callback (int, ReasonCode
            oder ``None``).

    Returns:
        ``True``, wenn der Code einen Fehlschlag anzeigt.
    """
    failure = getattr(reason_code, "is_failure", None)
    if failure is not None:
        return bool(failure)
    try:
        return int(reason_code) != 0
    except (TypeError, ValueError):
        return reason_code is not None


class MqttSubscriber:
    """
    Asynchroner MQTT-Client fuer ESP32-Klimadaten.

    Verbindet zum Broker, abonniert die konfigurierten Topics und schreibt
    eingehende Messwerte in den uebergebenen :class:`ClimateBuffer`.
    Laeuft, bis die umgebende Task gecancelt wird.
    """

    def __init__(
        self,
        cfg: ClimateSensorsConfig,
        buffer: ClimateBuffer,
        retry_interval_s: float = DEFAULT_RETRY_INTERVAL_S,
        max_retries: int | None = None,
    ) -> None:
        """
        Initialisiert den Subscriber (verbindet noch nicht).

        Args:
            cfg: Klimasensor-Block aus der ``config.yaml``.
            buffer: Ziel-Puffer fuer eingehende Messwerte.
            retry_interval_s: Wartezeit zwischen Verbindungsversuchen.
            max_retries: Maximale Anzahl Verbindungsversuche; ``None``
                bedeutet unbegrenzt (Produktivbetrieb). Endliche Werte
                dienen Tests und Einmal-Laeufen.
        """
        self._cfg = cfg
        self._buffer = buffer
        self._retry_interval_s = retry_interval_s
        self._max_retries = max_retries
        self._client: Any = None

    async def run(self) -> None:
        """
        Verbindet zum Broker und pumpt Messwerte in den Buffer.

        Laeuft bis ``asyncio.CancelledError``. Graceful Degradation:
            * ``paho-mqtt`` fehlt -> Warnung, Rueckkehr (kein Crash).
            * Modul per Config deaktiviert -> Info-Log, Rueckkehr.
            * Broker nicht erreichbar -> Warnung + erneuter Versuch nach
              ``retry_interval_s`` (bzw. Rueckkehr nach ``max_retries``).

        Seiteneffekte:
            Schreibt via paho-Callback aus dem Netzwerk-Thread in den
            :class:`ClimateBuffer`.
        """
        if not self._cfg.enabled:
            logger.info("Klimasensoren deaktiviert (climate_sensors.enabled=false).")
            return
        if mqtt_client is None:
            logger.warning(
                "paho-mqtt ist nicht installiert — Klimadaten werden "
                "uebersprungen. Installieren mit: pip install paho-mqtt"
            )
            return

        attempts = 0
        try:
            while True:
                attempts += 1
                connected = await self._connect_once()
                if connected:
                    # Bis zur Cancellation laufen lassen; paho pumpt im
                    # eigenen Netzwerk-Thread weiter.
                    await asyncio.Event().wait()
                if self._max_retries is not None and attempts >= self._max_retries:
                    logger.warning(
                        "MQTT-Broker %s:%s nach %d Versuch(en) nicht "
                        "erreichbar — Klimadaten bleiben leer.",
                        self._cfg.broker_host,
                        self._cfg.broker_port,
                        attempts,
                    )
                    return
                await asyncio.sleep(self._retry_interval_s)
        finally:
            await self.close()

    async def close(self) -> None:
        """
        Trennt die Broker-Verbindung sauber (idempotent).

        Seiteneffekte:
            Stoppt den paho-Netzwerk-Thread und schliesst den Socket.
            Ohne aktive Verbindung ist der Aufruf ein No-Op.
        """
        client, self._client = self._client, None
        if client is None:
            return
        try:
            # Erst disconnect (sendet das DISCONNECT-Paket über den noch
            # laufenden Netzwerk-Loop), dann loop_stop — paho-Shutdown-Reihenfolge.
            await asyncio.to_thread(client.disconnect)
            await asyncio.to_thread(client.loop_stop)
            logger.info("MQTT-Verbindung getrennt.")
        except Exception:  # noqa: BLE001 - Cleanup darf nie crashen
            logger.debug("Fehler beim MQTT-Disconnect (ignoriert).", exc_info=True)

    # ------------------------------------------------------------------
    # Intern
    # ------------------------------------------------------------------

    async def _connect_once(self) -> bool:
        """
        Ein einzelner Verbindungsversuch inkl. Topic-Subscribe.

        Returns:
            ``True`` bei erfolgreicher Verbindung, sonst ``False``
            (Warnung wurde geloggt, Aufrufer entscheidet ueber Retry).
        """
        client = self._make_client()
        # self._client früh setzen, damit close() den Client auch dann erreicht
        # und sauber schliesst, wenn run() während des (blockierenden) connect
        # gecancelt wird (sonst bliebe der Socket bis zum GC offen).
        self._client = client
        try:
            await asyncio.to_thread(
                client.connect, self._cfg.broker_host, self._cfg.broker_port
            )
        except (OSError, ValueError) as exc:
            logger.warning(
                "MQTT-Broker %s:%s nicht erreichbar: %s",
                self._cfg.broker_host,
                self._cfg.broker_port,
                exc,
            )
            return False
        # Netzwerk-Loop im paho-eigenen Thread starten. Abonniert wird sofort
        # UND im on_connect-Callback: sofort, damit keine Nachricht verloren
        # geht; im Callback, damit die Subscriptions auch nach einem
        # Auto-Reconnect von paho wieder aktiv sind (idempotent beim Broker).
        client.loop_start()
        self._subscribe_all(client)
        self._client = client
        logger.info(
            "MQTT verbunden (%s:%s), Topics: %s",
            self._cfg.broker_host,
            self._cfg.broker_port,
            ", ".join(self._cfg.topics),
        )
        return True

    def _make_client(self) -> Any:
        """
        Baut einen paho-Client (kompatibel zu paho-mqtt v1 und v2).

        Returns:
            Konfigurierter, noch nicht verbundener paho-Client.
        """
        try:
            # paho-mqtt >= 2.0 verlangt eine explizite Callback-API-Version.
            client = mqtt_client.Client(
                callback_api_version=mqtt_client.CallbackAPIVersion.VERSION2
            )
        except (AttributeError, TypeError):
            client = mqtt_client.Client()
        if self._cfg.username:
            client.username_pw_set(self._cfg.username, self._cfg.password)
        # paho reconnectet bei laufendem Netzwerk-Loop selbststaendig —
        # mit gedeckeltem Backoff statt Dauerfeuer. Defensiv via getattr,
        # damit auch abgespeckte Test-Doubles funktionieren.
        reconnect_delay_set = getattr(client, "reconnect_delay_set", None)
        if reconnect_delay_set is not None:
            reconnect_delay_set(min_delay=1, max_delay=_RECONNECT_MAX_DELAY_S)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        return client

    def _subscribe_all(self, client: Any) -> None:
        """
        Abonniert alle konfigurierten Topics auf dem gegebenen Client.

        Args:
            client: Verbundener (oder verbindender) paho-Client.
        """
        for topic in self._cfg.topics:
            result = client.subscribe(topic, qos=self._cfg.qos)
            # paho liefert (result_code, mid); result_code != 0 heisst „nicht
            # gesendet" (z.B. keine Verbindung) — sonst blieben Klimadaten still
            # leer (Whitebox-Prinzip). Test-Doubles dürfen None zurückgeben.
            rc = result[0] if isinstance(result, tuple) else result
            if rc not in (0, None):
                logger.warning(
                    "MQTT-Subscribe für '%s' fehlgeschlagen (rc=%s).", topic, rc
                )
            else:
                logger.debug(
                    "MQTT-Topic abonniert: %s (qos=%d)", topic, self._cfg.qos
                )

    def _on_connect(
        self,
        client: Any,
        _userdata: Any,
        _flags: Any,
        reason_code: Any,
        _properties: Any = None,
    ) -> None:
        """
        paho-Callback (Netzwerk-Thread!): abonniert nach jedem (Re-)Connect.

        Das Subscribe hier stellt sicher, dass die Topics auch nach einem
        Auto-Reconnect von paho wieder abonniert sind (bei ``clean_session``
        gehen sie sonst verloren). Beim Erst-Connect ist das gegenueber
        :meth:`_subscribe_all` idempotent.

        Kompatibel mit paho-mqtt v1 (``rc: int``) und v2 (``ReasonCode``).

        Args:
            client: paho-Client, auf dem abonniert wird.
            _userdata: Userdata (ungenutzt).
            _flags: Verbindungs-Flags (ungenutzt).
            reason_code: Ergebnis des Verbindungsaufbaus (0 = Erfolg).
            _properties: MQTT-v5-Properties (nur paho v2, ungenutzt).
        """
        if _is_failure(reason_code):
            logger.warning("MQTT-Verbindung vom Broker abgelehnt: %s", reason_code)
            return
        self._subscribe_all(client)

    def _on_disconnect(self, _client: Any, _userdata: Any, *args: Any) -> None:
        """
        paho-Callback (Netzwerk-Thread!): loggt Verbindungsabbrueche.

        paho reconnectet bei laufendem Netzwerk-Loop automatisch (siehe
        ``reconnect_delay_set``); hier wird nur Whitebox-transparent geloggt.
        Kompatibel mit paho-mqtt v1 (``rc``) und v2 (``flags, reason, props``).

        Args:
            _client: paho-Client (ungenutzt).
            _userdata: Userdata (ungenutzt).
            *args: Versionsabhaengige Restargumente; enthaelt den Reason-Code.
        """
        reason = args[1] if len(args) >= 2 else (args[0] if args else None)
        if _is_failure(reason):
            logger.warning(
                "MQTT-Verbindung zum Broker verloren (%s) — automatischer "
                "Reconnect laeuft.",
                reason,
            )

    def _on_message(self, _client: Any, _userdata: Any, message: Any) -> None:
        """
        paho-Callback (Netzwerk-Thread!): schreibt einen Messwert in den Buffer.

        Unbekannte Topics und ungueltige Payloads werden verworfen und nur
        per Debug-Log vermerkt.

        Args:
            _client: paho-Client (ungenutzt).
            _userdata: Userdata (ungenutzt).
            message: paho-Message mit ``.topic`` und ``.payload``.
        """
        metric = topic_to_metric(message.topic)
        if metric is None:
            logger.debug("MQTT-Topic ohne Metrik-Mapping verworfen: %s", message.topic)
            return
        value = parse_payload(message.payload)
        if value is None:
            return
        self._buffer.add(metric, value, datetime.now(timezone.utc))
