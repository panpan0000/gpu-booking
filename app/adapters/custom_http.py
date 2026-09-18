import json
import os

import httpx

from .base import GpuCardStatus, GpuStatusAdapter


class CustomHttpAdapter(GpuStatusAdapter):
    """对接已有的自定义 HTTP API。

    extra_config 支持字段映射:
      {"items_path": "data.gpus", "map": {"node": "node_name", "gpu_index": "index", ...}}
    默认假设返回 list[dict], 字段名与 GpuCardStatus 一致或相近。
    """

    async def fetch(self) -> list[GpuCardStatus]:
        cfg = json.loads(self.cluster.extra_config or "{}")
        headers = cfg.get("headers", {})
        if self.cluster.token_env:
            headers.setdefault("Authorization", f"Bearer {os.getenv(self.cluster.token_env, '')}")
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(self.cluster.endpoint, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        items = data
        for key in cfg.get("items_path", "").split("."):
            if key:
                items = items[key]
        fmap = cfg.get("map", {})
        return [self._normalize(item, fmap) for item in items]

    def _normalize(self, item: dict, fmap: dict) -> GpuCardStatus:
        def g(name: str, default=None):
            return item.get(fmap.get(name, name), default)

        return GpuCardStatus(
            cluster_name=self.cluster.name,
            node_name=str(g("node_name", "")),
            gpu_index=g("gpu_index"),
            allocated=bool(g("allocated", False)),
            pod_name=str(g("pod_name", "") or ""),
            namespace=str(g("namespace", "") or ""),
            user_name=str(g("user_name", "") or ""),
            util=g("util"),
            mem_used=g("mem_used"),
            mem_total=g("mem_total"),
        )
