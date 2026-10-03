import unittest
from unittest.mock import Mock, patch
import pandas as pd
import numpy as np
from datetime import datetime, timezone

# Тесты для forecaster.py
class TestAdaptiveRiskAwareForecaster(unittest.TestCase):
    
    def setUp(self):
        from autoscaler.forecaster import AdaptiveRiskAwareForecaster
        self.forecaster = AdaptiveRiskAwareForecaster(
            alpha=0.3,
            beta=0.1,
            lambda_plus=0.5,
            lambda_minus=0.05,
            kappa=1.0
        )
    
    def test_forecast_single_value(self):
        """Тест прогноза для одного значения"""
        series = pd.Series([10.0])
        result = self.forecaster.run(series, 1)
        
        # Для одного значения прогноз должен быть равен значению
        self.assertEqual(result.base_forecast, 10.0)
        self.assertEqual(result.corrected_forecast, 10.0)
    
    def test_forecast_constant_series(self):
        """Тест прогноза для постоянного ряда"""
        series = pd.Series([10.0, 10.0, 10.0, 10.0])
        result = self.forecaster.run(series, 1)
        
        # Прогноз должен быть близким к 10.0
        self.assertAlmostEqual(result.base_forecast, 10.0, places=2)
    
    def test_forecast_increasing_series(self):
        """Тест прогноза для возрастающего ряда"""
        series = pd.Series([10.0, 12.0, 14.0, 16.0])
        result = self.forecaster.run(series, 1)
        
        # Прогноз должен быть выше текущего значения
        self.assertGreater(result.base_forecast, 16.0)

# Тесты для decision.py
class TestScalingDecisionEngine(unittest.TestCase):
    
    def setUp(self):
        from autoscaler.config import ScalingConfig, ServiceConfig
        from autoscaler.decision import ScalingDecisionEngine
        
        scaling_config = ScalingConfig(
            cooldown_seconds=30,
            scale_up_step_limit=2,
            scale_down_step_limit=1
        )
        
        service_config = ServiceConfig(
            name="test-service",
            target_per_replica=5.0,
            min_replicas=1,
            max_replicas=10
        )
        
        self.engine = ScalingDecisionEngine(scaling_config)
        self.service = service_config
    
    def test_no_change_needed(self):
        """Тест когда изменение не требуется"""
        decision = self.engine.decide(
            service=self.service,
            current_replicas=2,
            predicted_load=10.0
        )
        
        self.assertFalse(decision.changed)
        self.assertEqual(decision.final_replicas, 2)
    
    def test_scale_up(self):
        """Тест масштабирования вверх"""
        decision = self.engine.decide(
            service=self.service,
            current_replicas=2,
            predicted_load=15.0  # Нужно 3 реплики
        )
        
        self.assertTrue(decision.changed)
        self.assertEqual(decision.final_replicas, 3)  # Ограниченный шаг масштабирования
    
    def test_scale_down(self):
        """Тест масштабирования вниз"""
        decision = self.engine.decide(
            service=self.service,
            current_replicas=5,
            predicted_load=5.0  # Нужно 1 реплику
        )
        
        self.assertTrue(decision.changed)
        self.assertEqual(decision.final_replicas, 4)  # Ограниченный шаг масштабирования

# Тесты для metrics_collector.py
class TestPrometheusClient(unittest.TestCase):
    
    @patch('requests.get')
    def test_query_range_success(self, mock_get):
        """Тест успешного запроса к Prometheus"""
        from autoscaler.metrics_collector import PrometheusClient
        
        # Настроим мок для ответа от Prometheus
        mock_response = Mock()
        mock_response.json.return_value = {
            "status": "success",
            "data": {
                "result": [
                    {
                        "metric": {"__name__": "test_metric"},
                        "values": [[1609459200, "10"], [1609459260, "12"]]
                    }
                ]
            }
        }
        mock_get.return_value = mock_response
        
        client = PrometheusClient("http://prometheus:9090")
        result = client.query_range("test_metric", 60, 60)
        
        self.assertIsInstance(result, pd.Series)
        self.assertEqual(len(result), 2)

if __name__ == '__main__':
    unittest.main()