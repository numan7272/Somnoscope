"""
Tests fuer die Klima-Anbindung (:mod:`iot`).

Abgedeckt (ohne echten Broker, ohne echte Sockets, paho-mqtt optional):
    * :class:`iot.ClimateBuffer` — korrekte Mittelwerte je Metrik, exakte
      Report-Keys (``avg_co2``/``avg_temp``/``avg_humidity``), ``None`` bei
      fehlenden Daten, inklusives Zeitfenster, Groessen- und
      Altersbegrenzung, Verwerfen unbekannter Metriken.
    * :func:`iot.topic_to_metric` — Suffix-Mapping der config.yaml-Topics
      (``bedroom/co2`` usw.), ``temp``-Alias, unbekannte Topics -> ``None``.
    * :func:`iot.parse_payload` — defensives float-Parsing (str/bytes,
      Muell wird verworfen).
    * :class:`iot.MqttSubscriber` — Graceful Degradation: konstruierbar mit
      :class:`core.config_loader.ClimateSensorsConfig`; ``run()`` crasht
      weder ohne paho-mqtt noch bei unerreichbarem Broker; sauberes
      Beenden per Cancellation; ``close()`` ist ohne Verbindung ein No-Op.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from core.config_loader import ClimateSensorsConfig
from iot import ClimateBuffer, MqttSubscriber, parse_payload, topic_to_metric

#: Fester Referenzzeitpunkt fuer deterministische Fenster-Tests.
T0 = datetime(2026, 7, 6, 23, 0, 0, tzinfo=timezone.utc)

#: Exakte Keys des ``SleepReport["climate"]``-Blocks.
REPORT_KEYS = {"avg_co2", "avg_temp", "avg_humidity"}


def make_cfg(**overrides: Any) -> ClimateSensorsConfig:
    """
    Baut eine :class:`ClimateSensorsConfig` mit den config.yaml-Defaults.

    Args:
        **overrides: Felder, die vom Default abweichen sollen.

    Returns:
        Eingefrorene Klimasensor-Konfiguration fuer Tests.
    """
    defaults: dict[str, Any] = {
        "enabled": True,
        "broker_host": "localhost",
        "broker_port": 1883,
        "topics": ("bedroom/co2", "bedroom/temperature", "bedroom/humidity"),
        "qos": 1,
        "username": None,
        "password": None,
    }
    defaults.update(overrides)
    return ClimateSensorsConfig(**defaults)


# ----------------------------------------------------------------------------
# ClimateBuffer: Mittelwerte und Report-Format
# ----------------------------------------------------------------------------

def test_averages_exact_keys_and_values() -> None:
    """Mittelwerte je Metrik stimmen; das Dict hat exakt die Report-Keys."""
    buf = ClimateBuffer()
    buf.add("co2", 800.0, T0 + timedelta(minutes=1))
    buf.add("co2", 900.0, T0 + timedelta(minutes=2))
    buf.add("temperature", 20.0, T0 + timedelta(minutes=1))
    buf.add("temperature", 22.0, T0 + timedelta(minutes=3))
    buf.add("humidity", 45.0, T0 + timedelta(minutes=2))

    result = buf.averages_between(T0, T0 + timedelta(hours=8))

    assert set(result.keys()) == REPORT_KEYS
    assert result["avg_co2"] == pytest.approx(850.0)
    assert result["avg_temp"] == pytest.approx(21.0)
    assert result["avg_humidity"] == pytest.approx(45.0)


def test_averages_none_without_data() -> None:
    """Leerer Buffer liefert die drei Keys, alle mit ``None``."""
    buf = ClimateBuffer()
    result = buf.averages_between(T0, T0 + timedelta(hours=8))
    assert result == {"avg_co2": None, "avg_temp": None, "avg_humidity": None}


def test_averages_none_per_metric_without_data() -> None:
    """Nur Metriken mit Daten bekommen einen Wert, der Rest bleibt ``None``."""
    buf = ClimateBuffer()
    buf.add("co2", 812.5, T0 + timedelta(minutes=5))
    result = buf.averages_between(T0, T0 + timedelta(hours=8))
    assert result["avg_co2"] == pytest.approx(812.5)
    assert result["avg_temp"] is None
    assert result["avg_humidity"] is None


def test_averages_window_filter_inclusive_bounds() -> None:
    """Nur Punkte mit ``start <= ts <= end`` zaehlen; Grenzen sind inklusiv."""
    buf = ClimateBuffer()
    start = T0
    end = T0 + timedelta(hours=1)
    buf.add("co2", 111.0, start - timedelta(seconds=1))  # vor dem Fenster
    buf.add("co2", 400.0, start)  # exakt Fensterbeginn
    buf.add("co2", 600.0, end)  # exakt Fensterende
    buf.add("co2", 999.0, end + timedelta(seconds=1))  # nach dem Fenster

    result = buf.averages_between(start, end)
    assert result["avg_co2"] == pytest.approx(500.0)


def test_unknown_metric_is_ignored() -> None:
    """Unbekannte Metriken werden verworfen und beeinflussen nichts."""
    buf = ClimateBuffer()
    buf.add("radon", 42.0, T0)  # kein Crash, kein Effekt
    result = buf.averages_between(T0 - timedelta(hours=1), T0 + timedelta(hours=1))
    assert result == {"avg_co2": None, "avg_temp": None, "avg_humidity": None}


# ----------------------------------------------------------------------------
# ClimateBuffer: Groessen- und Altersbegrenzung
# ----------------------------------------------------------------------------

def test_size_limit_drops_oldest_points() -> None:
    """Bei vollem Ringpuffer fallen die aeltesten Punkte zuerst raus."""
    buf = ClimateBuffer(max_points_per_metric=3)
    for i, value in enumerate([1.0, 2.0, 3.0, 4.0]):
        buf.add("co2", value, T0 + timedelta(seconds=i))

    result = buf.averages_between(T0, T0 + timedelta(minutes=1))
    # 1.0 ist verdraengt -> Mittel aus 2, 3, 4.
    assert result["avg_co2"] == pytest.approx(3.0)


def test_age_limit_drops_stale_points() -> None:
    """Punkte aelter als ``max_age`` relativ zum neuesten Punkt verschwinden."""
    buf = ClimateBuffer(max_age=timedelta(days=3))
    stale_ts = T0 - timedelta(days=4)
    buf.add("co2", 500.0, stale_ts)
    buf.add("co2", 700.0, T0)  # loest das Pruning des alten Punkts aus

    result = buf.averages_between(stale_ts - timedelta(hours=1), T0)
    # Nur der frische Punkt darf noch da sein.
    assert result["avg_co2"] == pytest.approx(700.0)


# ----------------------------------------------------------------------------
# Topic->Metric-Mapping und Payload-Parsing (reine Funktionen)
# ----------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("topic", "expected"),
    [
        ("bedroom/co2", "co2"),
        ("bedroom/temperature", "temperature"),
        ("bedroom/humidity", "humidity"),
        ("livingroom/sensors/temp", "temperature"),  # Alias + tieferer Pfad
        ("co2", "co2"),  # Topic ohne Praefix
        ("bedroom/pressure", None),  # unbekannte Metrik
        ("bedroom/", None),  # leeres Suffix
    ],
)
def test_topic_to_metric(topic: str, expected: str | None) -> None:
    """Suffix nach dem letzten ``/`` entscheidet; Unbekanntes ergibt ``None``."""
    assert topic_to_metric(topic) == expected


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ("812.5", 812.5),
        (b"812.5", 812.5),
        ("  21.3\n", 21.3),  # Whitespace wird toleriert
        ("-4.5", -4.5),
        ("kaputt", None),
        (b"\xff\xfe", None),  # kein UTF-8
        ("", None),
    ],
)
def test_parse_payload(payload: bytes | str, expected: float | None) -> None:
    """Gueltige Zahlen werden geparst, Muell wird verworfen (``None``)."""
    result = parse_payload(payload)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


# ----------------------------------------------------------------------------
# MqttSubscriber: Graceful Degradation (ohne paho, ohne Broker, ohne Sockets)
# ----------------------------------------------------------------------------

def test_subscriber_constructible_with_config() -> None:
    """Der Subscriber laesst sich mit einer ClimateSensorsConfig bauen."""
    sub = MqttSubscriber(make_cfg(), ClimateBuffer())
    assert isinstance(sub, MqttSubscriber)


@pytest.mark.asyncio
async def test_run_without_paho_does_not_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fehlt paho-mqtt, warnt ``run()`` und kehrt zurueck — kein Crash."""
    import iot.mqtt_subscriber as mod

    monkeypatch.setattr(mod, "mqtt_client", None)
    sub = MqttSubscriber(make_cfg(), ClimateBuffer())
    await asyncio.wait_for(sub.run(), timeout=2.0)  # darf nicht haengen


@pytest.mark.asyncio
async def test_run_disabled_returns_immediately() -> None:
    """Deaktiviertes Modul (enabled=false): ``run()`` kehrt sofort zurueck."""
    sub = MqttSubscriber(make_cfg(enabled=False), ClimateBuffer())
    await asyncio.wait_for(sub.run(), timeout=2.0)


@pytest.mark.asyncio
async def test_run_broker_unreachable_does_not_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Broker nicht erreichbar: Warnung + Rueckkehr nach ``max_retries``."""
    import iot.mqtt_subscriber as mod

    class RefusingClient:
        """Fake-paho-Client, dessen ``connect`` die Verbindung verweigert."""

        def username_pw_set(self, *_args: Any) -> None:
            pass

        def connect(self, *_args: Any, **_kwargs: Any) -> None:
            raise ConnectionRefusedError("Verbindung abgelehnt (Testdouble).")

    class FakePahoModule:
        """Minimales Modul-Double: nur ``Client`` (v1-Signatur)."""

        Client = RefusingClient

    monkeypatch.setattr(mod, "mqtt_client", FakePahoModule)
    sub = MqttSubscriber(
        make_cfg(), ClimateBuffer(), retry_interval_s=0.01, max_retries=2
    )
    await asyncio.wait_for(sub.run(), timeout=2.0)  # kein Crash, kein Haengen


@pytest.mark.asyncio
async def test_run_connect_success_and_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Erfolgreiche (Fake-)Verbindung: Topics abonniert, Cancel raeumt auf."""
    import iot.mqtt_subscriber as mod

    class FakeClient:
        """Fake-paho-Client, der Aufrufe protokolliert statt zu senden."""

        def __init__(self) -> None:
            self.subscribed: list[tuple[str, int]] = []
            self.loop_started = False
            self.loop_stopped = False
            self.disconnected = False
            self.on_message: Any = None

        def username_pw_set(self, *_args: Any) -> None:
            pass

        def connect(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def loop_start(self) -> None:
            self.loop_started = True

        def loop_stop(self) -> None:
            self.loop_stopped = True

        def disconnect(self) -> None:
            self.disconnected = True

        def subscribe(self, topic: str, qos: int = 0) -> None:
            self.subscribed.append((topic, qos))

    created: list[FakeClient] = []

    class FakePahoModule:
        """Modul-Double, das erzeugte Clients zur Inspektion sammelt."""

        @staticmethod
        def Client(*_args: Any, **_kwargs: Any) -> FakeClient:
            client = FakeClient()
            created.append(client)
            return client

    monkeypatch.setattr(mod, "mqtt_client", FakePahoModule)
    cfg = make_cfg()
    buf = ClimateBuffer()
    sub = MqttSubscriber(cfg, buf)

    task = asyncio.create_task(sub.run())
    await asyncio.sleep(0.05)  # run() bis zum Warten auf Cancellation bringen
    assert created, "run() muss einen Client erzeugt haben."
    client = created[0]
    assert client.loop_started is True
    assert [t for t, _ in client.subscribed] == list(cfg.topics)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert client.loop_stopped is True
    assert client.disconnected is True

    # Eingehende Nachricht ueber den registrierten Callback -> Buffer.
    class FakeMessage:
        """Nachrichten-Double mit ``topic``/``payload`` wie bei paho."""

        topic = "bedroom/co2"
        payload = b"812.5"

    sub._on_message(client, None, FakeMessage())
    now = datetime.now(timezone.utc)
    result = buf.averages_between(now - timedelta(minutes=1), now + timedelta(minutes=1))
    assert result["avg_co2"] == pytest.approx(812.5)


@pytest.mark.asyncio
async def test_close_without_connection_is_noop() -> None:
    """``close()`` ohne vorherige Verbindung ist ein sicherer No-Op."""
    sub = MqttSubscriber(make_cfg(), ClimateBuffer())
    await sub.close()  # darf nicht werfen
    await sub.close()  # idempotent
