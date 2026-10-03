from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

import yaml

logger = logging.getLogger(__name__)


@dataclass
class PrometheusConfig:
    url: str
    timeout_seconds: int = 10


@dataclass
class PollerConfig:
    interval_seconds: int = 60
    history_window_minutes: int = 120
    step_seconds: int = 30
    forecast_horizon_minutes: int = 10


@dataclass
class ScalingConfig:
    cooldown_seconds: int = 180
    scale_up_step_limit: int = 4
    scale_down_step_limit: int = 2


@dataclass
class ServiceConfig:
    name: str
    namespace: str
    deployment: str
    query: str
    target_per_replica: float
    min_replicas: int = 1
    max_replicas: int = 10
    # Параметры единственного алгоритма прогнозирования (ARAF, см. forecaster.py)
    alpha: float = 0.3          # скорость адаптации уровня
    beta: float = 0.1           # скорость адаптации тренда
    lambda_plus: float = 0.5    # скорость роста риск-margin при недооценке
    lambda_minus: float = 0.05  # скорость затухания риск-margin при переоценке
    kappa: float = 1.0          # вес риск-поправки (~ C_under / C_over)


@dataclass
class AppConfig:
    prometheus: PrometheusConfig
    poller: PollerConfig
    scaling: ScalingConfig
    services: List[ServiceConfig] = field(default_factory=list)


def load_config(path: str | Path) -> AppConfig:
    """Читает YAML-файл конфигурации и превращает его в объект AppConfig."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Файл конфигурации не найден: {path}")

    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if not raw:
        raise ValueError("Конфигурационный файл пуст или некорректен")

    prom_raw = raw.get("prometheus", {})
    poller_raw = raw.get("poller", {})
    scaling_raw = raw.get("scaling", {})
    services_raw = raw.get("services", [])

    if not services_raw:
        raise ValueError("В конфигурации не указано ни одного сервиса (services)")

    prometheus = PrometheusConfig(
        url=prom_raw["url"],
        timeout_seconds=prom_raw.get("timeout_seconds", 10),
    )

    poller = PollerConfig(
        interval_seconds=poller_raw.get("interval_seconds", 60),
        history_window_minutes=poller_raw.get("history_window_minutes", 120),
        step_seconds=poller_raw.get("step_seconds", 30),
        forecast_horizon_minutes=poller_raw.get("forecast_horizon_minutes", 10),
    )

    scaling = ScalingConfig(
        cooldown_seconds=scaling_raw.get("cooldown_seconds", 180),
        scale_up_step_limit=scaling_raw.get("scale_up_step_limit", 4),
        scale_down_step_limit=scaling_raw.get("scale_down_step_limit", 2),
    )

    services = []
    for svc in services_raw:
        try:
            services.append(
                ServiceConfig(
                    name=svc["name"],
                    namespace=svc["namespace"],
                    deployment=svc["deployment"],
                    query=svc["query"],
                    target_per_replica=float(svc["target_per_replica"]),
                    min_replicas=int(svc.get("min_replicas", 1)),
                    max_replicas=int(svc.get("max_replicas", 10)),
                    alpha=float(svc.get("alpha", 0.3)),
                    beta=float(svc.get("beta", 0.1)),
                    lambda_plus=float(svc.get("lambda_plus", 0.5)),
                    lambda_minus=float(svc.get("lambda_minus", 0.05)),
                    kappa=float(svc.get("kappa", 1.0)),
                )
            )
        except KeyError as e:
            raise ValueError(f"В описании сервиса отсутствует обязательное поле: {e}")

    logger.info("Конфигурация загружена: %d сервис(ов)", len(services))
    return AppConfig(prometheus=prometheus, poller=poller, scaling=scaling, services=services)
