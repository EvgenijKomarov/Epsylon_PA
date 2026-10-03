from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from typing import Dict

from .config import ScalingConfig, ServiceConfig

logger = logging.getLogger(__name__)


@dataclass
class ScalingDecision:
    service_name: str
    current_replicas: int
    predicted_load: float
    desired_replicas: int  # то, что "хотелось бы" по чистому расчёту
    final_replicas: int  # то, что реально будет применено (после лимитов/cooldown)
    changed: bool
    reason: str


class ScalingDecisionEngine:
    """
    Переводит прогноз нагрузки в конкретное число реплик, с учётом:
      - min/max реплик сервиса;
      - ограничения на резкое изменение числа реплик за одну итерацию;
      - cooldown-периода между двумя изменениями одного сервиса
        (защита от "дребезга").
    """

    def __init__(self, scaling_config: ScalingConfig):
        self.scaling_config = scaling_config
        self._last_scale_time: Dict[str, float] = {}

    @staticmethod
    def _raw_target_replicas(predicted_load: float, target_per_replica: float) -> int:
        if target_per_replica <= 0:
            raise ValueError("target_per_replica должен быть положительным числом")
        return max(1, math.ceil(predicted_load / target_per_replica))

    def _in_cooldown(self, service_name: str) -> bool:
        last = self._last_scale_time.get(service_name)
        if last is None:
            return False
        return (time.monotonic() - last) < self.scaling_config.cooldown_seconds

    def decide(
        self,
        service: ServiceConfig,
        current_replicas: int,
        predicted_load: float,
    ) -> ScalingDecision:
        desired = self._raw_target_replicas(predicted_load, service.target_per_replica)
        desired = min(max(desired, service.min_replicas), service.max_replicas)

        if desired == current_replicas:
            return ScalingDecision(
                service_name=service.name,
                current_replicas=current_replicas,
                predicted_load=predicted_load,
                desired_replicas=desired,
                final_replicas=current_replicas,
                changed=False,
                reason="Прогноз не требует изменения количества реплик",
            )

        if self._in_cooldown(service.name):
            return ScalingDecision(
                service_name=service.name,
                current_replicas=current_replicas,
                predicted_load=predicted_load,
                desired_replicas=desired,
                final_replicas=current_replicas,
                changed=False,
                reason="Пропущено: активен cooldown-период после предыдущего масштабирования",
            )

        # Ограничиваем резкость изменения (шаг вверх/вниз)
        if desired > current_replicas:
            step = min(desired - current_replicas, self.scaling_config.scale_up_step_limit)
            final = current_replicas + step
        else:
            step = min(current_replicas - desired, self.scaling_config.scale_down_step_limit)
            final = current_replicas - step

        changed = final != current_replicas
        if changed:
            self._last_scale_time[service.name] = time.monotonic()

        return ScalingDecision(
            service_name=service.name,
            current_replicas=current_replicas,
            predicted_load=predicted_load,
            desired_replicas=desired,
            final_replicas=final,
            changed=changed,
            reason="Масштабирование выполнено с учётом ограничения шага"
            if changed
            else "Итоговое число реплик совпало с текущим после ограничения шага",
        )
