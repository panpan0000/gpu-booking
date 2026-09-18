import os

import httpx

from .base import GpuCardStatus, GpuStatusAdapter

# DCGM exporter 常用指标
QUERIES = {
    "util": "DCGM_FI_DEV_GPU_UTIL",
    "mem_used": "DCGM_FI_DEV_FB_USED",
    "mem_total": "DCGM_FI_DEV_FB_TOTAL",
}


class PrometheusAdapter(GpuStatusAdapter):
    """通过 Prometheus HTTP API 拉 DCGM 指标。endpoint 为 Prometheus 地址。"""

    async def fetch(self) -> list[GpuCardStatus]:
        token = os.getenv(self.cluster.token_env, "") if self.cluster.token_env else ""
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        cards: dict[tuple[str, int], GpuCardStatus] = {}
        async with httpx.AsyncClient(timeout=20) as client:
            for kind, metric in QUERIES.items():
                resp = await client.get(
                    f"{self.cluster.endpoint}/api/v1/query",
                    params={"query": metric},
                    headers=headers,
                )
                resp.raise_for_status()
                for series in resp.json().get("data", {}).get("result", []):
                    labels = series.get("metric", {})
                    node = labels.get("Hostname") or labels.get("node") or labels.get("instance", "")
                    gpu = labels.get("gpu") or labels.get("GPU_I_ID")
                    if gpu is None:
                        continue
                    key = (node, int(gpu))
                    card = cards.setdefault(key, GpuCardStatus(
                        cluster_name=self.cluster.name, node_name=node, gpu_index=int(gpu)))
                    value = series.get("value", [None, None])[1]
                    try:
                        setattr(card, kind, int(float(value)))
                    except (TypeError, ValueError):
                        pass
                    card.pod_name = labels.get("pod", labels.get("exported_pod", ""))
                    card.namespace = labels.get("namespace", labels.get("exported_namespace", ""))
                    card.allocated = bool(card.pod_name) or (card.util or 0) > 0
        return list(cards.values())
