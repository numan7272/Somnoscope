"""
Trend-Analytics-Modul von Somnoscope.

Öffentliche API::

    from analytics import compute_trends

    trends = compute_trends(store.list_reports(limit=30))

:func:`compute_trends` verdichtet eine Liste von SleepReport-Dicts zu
einem JSON-serialisierbaren Trend-Dict (Mittelwerte, chronologische
Zeitreihen, Phasenverteilung, beste/schlechteste Nacht, Score-Streuung).
Details siehe :mod:`analytics.trends`.
"""

from __future__ import annotations

from analytics.trends import compute_trends

__all__ = ["compute_trends"]
