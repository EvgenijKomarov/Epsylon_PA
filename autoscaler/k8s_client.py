from __future__ import annotations

import logging

from kubernetes import client, config
from kubernetes.client.rest import ApiException

logger = logging.getLogger(__name__)


class K8sScalerError(RuntimeError):
    """Возникает при ошибке взаимодействия с Kubernetes API."""


class K8sScaler:
    """
    Обёртка над официальным Python-клиентом Kubernetes для чтения и
    изменения количества реплик Deployment.

    Автоматически определяет режим запуска:
      - внутри пода кластера (in-cluster config, через ServiceAccount);
      - локально, для разработки/защиты диплома (через ~/.kube/config).
    """

    def __init__(self):
        try:
            config.load_incluster_config()
            logger.info("Используется in-cluster конфигурация Kubernetes")
        except config.ConfigException:
            config.load_kube_config()
            logger.info("Используется локальный kubeconfig")

        self.apps_api = client.AppsV1Api()

    def get_replicas(self, namespace: str, deployment: str) -> int:
        try:
            dep = self.apps_api.read_namespaced_deployment(
                name=deployment, namespace=namespace
            )
            return dep.spec.replicas or 0
        except ApiException as e:
            raise K8sScalerError(
                f"Не удалось прочитать Deployment {namespace}/{deployment}: {e}"
            ) from e

    def scale(self, namespace: str, deployment: str, replicas: int) -> None:
        if replicas < 0:
            raise ValueError("Количество реплик не может быть отрицательным")

        patch_body = {"spec": {"replicas": replicas}}
        try:
            self.apps_api.patch_namespaced_deployment_scale(
                name=deployment, namespace=namespace, body=patch_body
            )
            logger.info(
                "Deployment %s/%s масштабирован до %d реплик",
                namespace,
                deployment,
                replicas,
            )
        except ApiException as e:
            raise K8sScalerError(
                f"Не удалось изменить количество реплик {namespace}/{deployment}: {e}"
            ) from e
