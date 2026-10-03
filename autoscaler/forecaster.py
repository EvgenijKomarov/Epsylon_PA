from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class ForecastResult:
    base_forecast: float       # L_t + h*T_t, прогноз без поправки на риск
    risk_margin: float         # M_t, текущая асимметричная поправка
    corrected_forecast: float  # F*_t(h) = base_forecast + kappa * M_t
    level: float                # L_t
    trend: float                # T_t


class AdaptiveRiskAwareForecaster:
    """
    Адаптивный алгоритм прогнозирования нагрузки с асимметричной
    коррекцией риска недомасштабирования (Asymmetric Risk-Adaptive
    Forecasting, ARAF).

    Идея: классическое экспоненциальное сглаживание Хольта (уровень +
    тренд) даёт "средний" прогноз, минимизирующий симметричную ошибку.
    Для задачи автомасштабирования это неверная постановка: недооценка
    нагрузки (сервис не успел получить реплики) обходится значительно
    дороже, чем переоценка (несколько лишних подов постояли впустую).

    Алгоритм вводит отдельное состояние M_t - экспоненциально
    сглаженную ошибку прогноза с ДВУМЯ разными коэффициентами
    сглаживания в зависимости от знака ошибки: margin быстро растёт
    при недооценке (lambda_plus) и медленно затухает при переоценке
    (lambda_minus << lambda_plus). Итоговый прогноз - это базовый
    прогноз Хольта плюс эта риск-поправка, взвешенная коэффициентом
    kappa, который можно обосновать через соотношение стоимости
    недомасштабирования и стоимости избыточной реплики
    (аргумент в духе newsvendor-модели из исследования операций):

        kappa ~= C_under / C_over

    Формулы (t - шаг дискретизации временного ряда, h - горизонт в шагах):

        L_t = alpha*y_t + (1-alpha)*(L_{t-1} + T_{t-1})
        T_t = beta*(L_t - L_{t-1}) + (1-beta)*T_{t-1}

        e_t = y_t - (L_{t-1} + T_{t-1})                       # ошибка прогноза
        M_t = lambda_plus * e_t + (1-lambda_plus) * M_{t-1}     если e_t >= 0
        M_t = lambda_minus * e_t + (1-lambda_minus) * M_{t-1}   если e_t <  0
        M_t = max(M_t, 0)

        F(h)  = L_t + h*T_t                                   # базовый прогноз
        F*(h) = F(h) + kappa * M_t                             # итоговый прогноз
    """

    def __init__(
        self,
        alpha: float = 0.3,
        beta: float = 0.1,
        lambda_plus: float = 0.5,
        lambda_minus: float = 0.05,
        kappa: float = 1.0,
    ):
        if not (0 < alpha <= 1):
            raise ValueError("alpha должен быть в диапазоне (0, 1]")
        if not (0 < beta <= 1):
            raise ValueError("beta должен быть в диапазоне (0, 1]")
        if not (0 < lambda_minus <= lambda_plus <= 1):
            raise ValueError("Должно выполняться 0 < lambda_minus <= lambda_plus <= 1")
        if kappa < 0:
            raise ValueError("kappa не может быть отрицательным")

        self.alpha = alpha
        self.beta = beta
        self.lambda_plus = lambda_plus
        self.lambda_minus = lambda_minus
        self.kappa = kappa

    def run(self, series: pd.Series, horizon_steps: int) -> ForecastResult:
        """
        Прогоняет рекурсию по всей истории `series` и возвращает прогноз
        на `horizon_steps` шагов вперёд вместе с промежуточным состоянием
        (уровень, тренд, накопленный риск-margin) - удобно логировать
        отдельно для анализа поведения алгоритма в дипломе.
        """
        values = series.values.astype(float)
        n = len(values)

        if n == 0:
            return ForecastResult(0.0, 0.0, 0.0, 0.0, 0.0)
        if n == 1:
            v = float(values[0])
            return ForecastResult(v, 0.0, v, v, 0.0)

        level = float(values[0])
        trend = float(values[1] - values[0])
        margin = 0.0

        for t in range(1, n):
            y = float(values[t])

            # Ошибка одношагового прогноза, сделанного на предыдущем шаге
            one_step_forecast = level + trend
            error = y - one_step_forecast

            if error >= 0:
                margin = self.lambda_plus * error + (1 - self.lambda_plus) * margin
            else:
                margin = self.lambda_minus * error + (1 - self.lambda_minus) * margin
            margin = max(margin, 0.0)

            # Обновление уровня и тренда (рекурсия Хольта)
            new_level = self.alpha * y + (1 - self.alpha) * (level + trend)
            new_trend = self.beta * (new_level - level) + (1 - self.beta) * trend
            level, trend = new_level, new_trend

        base_forecast = level + horizon_steps * trend
        corrected = base_forecast + self.kappa * margin

        return ForecastResult(
            base_forecast=max(0.0, base_forecast),
            risk_margin=margin,
            corrected_forecast=max(0.0, corrected),
            level=level,
            trend=trend,
        )

    def predict(self, series: pd.Series, horizon_steps: int) -> float:
        """Возвращает только итоговое скорректированное значение прогноза."""
        return self.run(series, horizon_steps).corrected_forecast
