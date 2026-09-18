from .base import GpuCardStatus, GpuStatusAdapter
from .custom_http import CustomHttpAdapter
from .k8s_api import K8sApiAdapter
from .prometheus import PrometheusAdapter

ADAPTERS = {
    "k8s_api": K8sApiAdapter,
    "prometheus": PrometheusAdapter,
    "custom_http": CustomHttpAdapter,
}

__all__ = ["ADAPTERS", "GpuCardStatus", "GpuStatusAdapter"]
