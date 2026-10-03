from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
import time

from .config import AppConfig, ServiceConfig, load_config
from .decision import ScalingDecisionEngine
from .forecaster import AdaptiveRiskAwareForecaster
from .k8s_client import K8sScaler, K8sScalerError
from .metrics_collector import PrometheusClient, PrometheusQueryError

logger = logging.getLogger("autoscaler")

_stop_event = threading.Event()


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _handle_signal(signum, frame):
    logger.info("Получен сигнал остановки (%s), завершаю работу...", signum)
    _stop_event.set()


def process_service(
    service: ServiceConfig,
    app_config: AppConfig,
    prom_client: PrometheusClient,
    k8s_scaler: K8sScaler,
    decision_engine: ScalingDecisionEngine,
    dry_run: bool,
) -> None:
    """Один полный цикл обработки одного сервиса: метрики -> прогноз -> решение -> действие."""
    try:
        series = prom_client.query_range(
            promql=service.query,
            history_window_minutes=app_config.poller.history_window_minutes,
            step_seconds=app_config.poller.step_seconds,
        )
    except PrometheusQueryError as e:
        logger.error("[%s] Ошибка получения метрик: %s", service.name, e)
        return

    if series.empty:
        logger.warning("[%s] Нет данных метрик, пропускаю итерацию", service.name)
        return

    horizon_steps = max(
        1,
        (app_config.poller.forecast_horizon_minutes * 60) // app_config.poller.step_seconds,
    )

    forecaster = AdaptiveRiskAwareForecaster(
        alpha=service.alpha,
        beta=service.beta,
        lambda_plus=service.lambda_plus,
        lambda_minus=service.lambda_minus,
        kappa=service.kappa,
    )
    forecast = forecaster.run(series, horizon_steps=horizon_steps)
    predicted_load = forecast.corrected_forecast

    logger.debug(
        "[%s] базовый_прогноз=%.2f, риск_margin=%.2f, итоговый_прогноз=%.2f",
        service.name,
        forecast.base_forecast,
        forecast.risk_margin,
        forecast.corrected_forecast,
    )

    try:
        current_replicas = k8s_scaler.get_replicas(service.namespace, service.deployment)
    except K8sScalerError as e:
        logger.error("[%s] Ошибка чтения текущего состояния в k8s: %s", service.name, e)
        return

    decision = decision_engine.decide(
        service=service,
        current_replicas=current_replicas,
        predicted_load=predicted_load,
    )

    logger.info(
        "[%s] текущее=%d, прогноз_нагрузки=%.2f, желаемое=%d, "
        "итоговое=%d, изменено=%s (%s)",
        service.name,
        decision.current_replicas,
        decision.predicted_load,
        decision.desired_replicas,
        decision.final_replicas,
        decision.changed,
        decision.reason,
    )

    if decision.changed and not dry_run:
        try:
            k8s_scaler.scale(service.namespace, service.deployment, decision.final_replicas)
        except K8sScalerError as e:
            logger.error("[%s] Ошибка масштабирования: %s", service.name, e)
    elif decision.changed and dry_run:
        logger.info(
            "[%s] DRY-RUN: масштабирование до %d реплик НЕ применено",
            service.name,
            decision.final_replicas,
        )


def service_loop(
    service: ServiceConfig,
    app_config: AppConfig,
    prom_client: PrometheusClient,
    k8s_scaler: K8sScaler,
    decision_engine: ScalingDecisionEngine,
    dry_run: bool,
) -> None:
    """Бесконечный цикл опроса для одного сервиса, выполняется в отдельном потоке."""
    logger.info(
        "Запущен поток мониторинга сервиса '%s' (интервал=%d с)",
        service.name,
        app_config.poller.interval_seconds,
    )
    while not _stop_event.is_set():
        start = time.monotonic()
        try:
            process_service(
                service, app_config, prom_client, k8s_scaler, decision_engine, dry_run
            )
        except Exception:
            logger.exception("[%s] Необработанная ошибка в цикле обработки", service.name)

        elapsed = time.monotonic() - start
        sleep_time = max(0.0, app_config.poller.interval_seconds - elapsed)
        _stop_event.wait(sleep_time)


def run(config_path: str, dry_run: bool, verbose: bool) -> None:
    _setup_logging(verbose)
    logger.info("Запуск Predictive Autoscaler (dry_run=%s)", dry_run)

    app_config = load_config(config_path)
    prom_client = PrometheusClient(
        base_url=app_config.prometheus.url,
        timeout_seconds=app_config.prometheus.timeout_seconds,
    )
    k8s_scaler = K8sScaler()
    decision_engine = ScalingDecisionEngine(app_config.scaling)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    threads = []
    for service in app_config.services:
        t = threading.Thread(
            target=service_loop,
            args=(service, app_config, prom_client, k8s_scaler, decision_engine, dry_run),
            name=f"loop-{service.name}",
            daemon=True,
        )
        t.start()
        threads.append(t)

    # Главный поток просто ждёт сигнала остановки
    while not _stop_event.is_set():
        _stop_event.wait(1.0)

    logger.info("Ожидаю завершения потоков...")
    for t in threads:
        t.join(timeout=5)
    logger.info("Autoscaler остановлен")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Predictive autoscaler для микросервисов в Kubernetes "
        "на основе прогнозирования временных рядов метрик из Prometheus/Grafana."
    )
    parser.add_argument(
        "-c", "--config", default="config.yaml", help="Путь к YAML-файлу конфигурации"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только логировать решения, не изменять реплики в k8s",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Подробное логирование (DEBUG)"
    )
    args = parser.parse_args()

    try:
        run(config_path=args.config, dry_run=args.dry_run, verbose=args.verbose)
    except Exception as e:
        logging.getLogger("autoscaler").critical("Фатальная ошибка запуска: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
