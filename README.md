# Predictive Autoscaler для микросервисов в Kubernetes

Программа опрашивает Prometheus (источник данных Grafana), строит
прогноз нагрузки методами анализа временных рядов и заранее (до того,
как нагрузка реально вырастет) выставляет нужное количество реплик
сервисов через Kubernetes API.

## Архитектура

```
Prometheus/Grafana  --query_range-->  metrics_collector.py
                                            |
                                            v
                                      forecaster.py  (Holt-Winters / линейный тренд / naive)
                                            |
                                            v
                                       decision.py  (min/max, шаг, cooldown)
                                            |
                                            v
                                      k8s_client.py  --patch replicas-->  Kubernetes API
```

Модули:

| Файл                      | Назначение                                              |
|---------------------------|----------------------------------------------------------|
| `config.py`                | Загрузка и валидация `config.yaml`                       |
| `metrics_collector.py`     | Запросы к Prometheus HTTP API (`/api/v1/query_range`)    |
| `forecaster.py`            | Единый алгоритм прогноза ARAF (см. ниже)                 |
| `decision.py`              | Перевод прогноза в целевое число реплик, cooldown, лимиты|
| `k8s_client.py`            | Чтение/изменение `spec.replicas` Deployment              |
| `main.py`                  | Оркестрация: по потоку на сервис, цикл опроса            |

## Установка

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Алгоритм прогнозирования: ARAF (Asymmetric Risk-Adaptive Forecasting)

Единственный алгоритм прогноза в проекте — адаптивное экспоненциальное
сглаживание уровня и тренда (метод Хольта) с **асимметричной
коррекцией риска недомасштабирования**. Идея: обычные модели
прогноза минимизируют симметричную ошибку, но для автоскейлинга
недооценка нагрузки (простой/деградация SLA) обходится значительно
дороже, чем переоценка (немного лишних подов). Алгоритм явно вводит
это в саму рекурсию прогноза — подробности и формулы см. в docstring
`autoscaler/forecaster.py::AdaptiveRiskAwareForecaster`.

Параметры на сервис:

| Параметр       | Смысл                                                        |
|----------------|---------------------------------------------------------------|
| `alpha`        | скорость адаптации уровня нагрузки (0..1)                    |
| `beta`         | скорость адаптации тренда (0..1)                             |
| `lambda_plus`  | скорость роста риск-margin при недооценке нагрузки           |
| `lambda_minus` | скорость затухания риск-margin при переоценке (`≪ lambda_plus`) |
| `kappa`        | вес риск-поправки, обоснуется как `C_недооценки / C_избыточной_реплики` |

## Конфигурация

Скопируйте `config.example.yaml` в `config.yaml` и укажите:
- адрес Prometheus;
- список сервисов с PromQL-запросом метрики нагрузки;
- `target_per_replica` — целевая нагрузка на одну реплику (аналог
  `targetAverageValue` в HPA);
- `min_replicas` / `max_replicas`;
- параметры алгоритма ARAF (`alpha`, `beta`, `lambda_plus`, `lambda_minus`, `kappa`).

## Запуск локально (например, против minikube/kind)

```bash
python -m autoscaler.main --config config.yaml --dry-run -v
```

Флаг `--dry-run` считает и логирует решения, но не применяет их к
кластеру — удобно для защиты диплома и отладки моделей без риска
что-то сломать. Уберите флаг, когда будете готовы к реальному
масштабированию.

## Запуск в кластере

```bash
docker build -t predictive-autoscaler:latest .
kubectl apply -f k8s/rbac.yaml
kubectl apply -f k8s/deployment.yaml
```

`ServiceAccount` из `k8s/rbac.yaml` даёт минимально необходимые права:
чтение и изменение `deployments`/`deployments/scale` в `apps/v1`.

## Идеи для расширения (раздел "научная новизна" в дипломе)

1. **Эмпирическое сравнение с baseline** — прогнать ARAF против
   штатного HPA и против симметричного Holt (kappa=0) на одних и тех же
   исторических данных, сравнить не только MAPE/RMSE, но и % нарушений
   SLA и переиспользование ресурсов — это и есть материал для главы
   с результатами эксперимента.
2. **Обоснование kappa через реальные затраты** — вывести коэффициент
   `kappa` из фактической стоимости пода в вашем кластере/облаке и
   оценки потерь от деградации сервиса (SLA-штрафы, потерянные
   транзакции), а не задавать его вручную.
3. **Метрики самого автоскейлера** — экспортировать в Prometheus
   `base_forecast`, `risk_margin`, `corrected_forecast` через
   `prometheus_client`, чтобы визуализировать поведение алгоритма в
   отдельном дашборде Grafana — наглядно для защиты.
4. **Custom Metrics API / kube-controller** — оформить логику как
   полноценный Kubernetes-контроллер (kopf/kubebuilder-style) вместо
   отдельного pod-приложения, работающего поверх Deployment API.
