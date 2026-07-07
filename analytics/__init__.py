"""
Trend-Analytics-Modul von Somnoscope.

Öffentliche API::

    from analytics import compute_trends

    trends = compute_trends(store.list_reports(limit=30))

:func:`compute_trends` verdichtet eine Liste von SleepReport-Dicts zu
einem JSON-serialisierbaren Trend-Dict (Mittelwerte, chronologische
Zeitreihen, Phasenverteilung, beste/schlechteste Nacht, Score-Streuung).
Details siehe :mod:`analytics.trends`.

Zusätzlich Daten-Export::

    from analytics import reports_to_csv, reports_to_json

:func:`reports_to_csv` / :func:`reports_to_json` serialisieren Reports
chronologisch als CSV (eine Zeile je Nacht) bzw. JSON (volle Reports).
Details siehe :mod:`analytics.export`.
"""

from __future__ import annotations

from analytics.export import reports_to_csv, reports_to_json
from analytics.trends import compute_trends

__all__ = ["compute_trends", "reports_to_csv", "reports_to_json"]
