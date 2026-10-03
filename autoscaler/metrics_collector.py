from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd
import requests

logger = logging.getLogger(__name__)


class PrometheusQueryError(RuntimeError):
    """Возникает при ошибке запроса к Prometheus API."""


class PrometheusClient:
    """
    Тонкий клиент поверх HTTP API Prometheus.

    Grafana сама по себе не предоставляет API временных рядов для внешних
    потребителей - она лишь визуализирует данные из своих Data Source.
    Поэтому опрос производится напрямую к Prometheus, который в
    подавляющем большинстве кластеров и является Data Source для Grafana.
    Если у вас другой backend (VictoriaMetrics, Thanos, Mimir) - он, как
    правило, совместим с этим же API.
    """

    def __init__(self, base_url: str, timeout_seconds: int = 10):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def query_range(
        self,
        promql: str,
        history_window_minutes: int,
        step_seconds: int,
        end_time: Optional[datetime] = None,
    ) -> pd.Series:
        """
        Выполняет запрос /api/v1/query_range и возвращает pandas.Series
        с индексом-временем (UTC) и значениями метрики.
        """
        end_time = end_time or datetime.now(timezone.utc)
        start_time = end_time - timedelta(minutes=history_window_minutes)

        params = {
            "query": promql,
            "start": start_time.timestamp(),
            "end": end_time.timestamp(),
            "step": f"{step_seconds}s",
        }

        url = f"{self.base_url}/api/v1/query_range"
        try:
            resp = requests.get(url, params=params, timeout=self.timeout_seconds)
            resp.raise_for_status()
        except requests.RequestException as e:
            raise PrometheusQueryError(f"Не удалось выполнить запрос к Prometheus: {e}") from e

        payload = resp.json()
        if payload.get("status") != "success":
            raise PrometheusQueryError(f"Prometheus вернул ошибку: {payload}")

        result = payload["data"]["result"]
        if not result:
            logger.warning("Пустой результат для запроса: %s", promql)
            return pd.Series(dtype="float64")

        # Если запрос возвращает несколько временных рядов (несколько
        # наборов меток), суммируем их поточечно - для целей автоскейлинга
        # обычно интересна агрегированная нагрузка.
        combined: Optional[pd.Series] = None
        for series in result:
            values = series["values"]  # [[timestamp, "value"], ...]
            idx = [datetime.fromtimestamp(v[0], tz=timezone.utc) for v in values]
            vals = [float(v[1]) for v in values]
            s = pd.Series(data=vals, index=idx)
            combined = s if combined is None else combined.add(s, fill_value=0.0)

        combined = combined.sort_index()
        return combined

    def instant_query(self, promql: str) -> Optional[float]:
        """Выполняет мгновенный запрос /api/v1/query и возвращает скаляр (если есть)."""
        url = f"{self.base_url}/api/v1/query"
        try:
            resp = requests.get(
                url, params={"query": promql}, timeout=self.timeout_seconds
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            raise PrometheusQueryError(f"Не удалось выполнить запрос к Prometheus: {e}") from e

        payload = resp.json()
        if payload.get("status") != "success":
            raise PrometheusQueryError(f"Prometheus вернул ошибку: {payload}")

        result = payload["data"]["result"]
        if not result:
            return None

        total = sum(float(r["value"][1]) for r in result)
        return total
