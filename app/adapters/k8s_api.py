import os

import httpx

from .base import GpuCardStatus, GpuStatusAdapter


class K8sApiAdapter(GpuStatusAdapter):
    """直接调 K8s API: 统计每个节点上 Running Pod 请求的 nvidia.com/gpu 数量。

    拿不到具体卡号, gpu_index 为空, 一个 Pod 一条记录(allocated=True, 占 N 卡则 Pod 重复 N 条)。
    用户信息从 Pod label (extra_config.user_label, 默认 "user") 读取。
    """

    async def fetch(self) -> list[GpuCardStatus]:
        import json
        cfg = json.loads(self.cluster.extra_config or "{}")
        user_label = cfg.get("user_label", "user")
        token = os.getenv(self.cluster.token_env, "") if self.cluster.token_env else ""
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        verify = cfg.get("ca_cert", False)

        cards: list[GpuCardStatus] = []
        async with httpx.AsyncClient(timeout=20, verify=verify, headers=headers) as client:
            resp = await client.get(
                f"{self.cluster.endpoint}/api/v1/pods",
                params={"fieldSelector": "status.phase=Running"},
            )
            resp.raise_for_status()
            pods = resp.json().get("items", [])

        for pod in pods:
            spec = pod.get("spec", {})
            meta = pod.get("metadata", {})
            node = spec.get("nodeName", "")
            if not node:
                continue
            n = 0
            for c in spec.get("containers", []):
                req = c.get("resources", {}).get("requests", {})
                lim = c.get("resources", {}).get("limits", {})
                n += int(lim.get("nvidia.com/gpu", req.get("nvidia.com/gpu", 0)) or 0)
            if n <= 0:
                continue
            for _ in range(n):
                cards.append(GpuCardStatus(
                    cluster_name=self.cluster.name,
                    node_name=node,
                    gpu_index=None,
                    allocated=True,
                    pod_name=meta.get("name", ""),
                    namespace=meta.get("namespace", ""),
                    user_name=meta.get("labels", {}).get(user_label, ""),
                ))
        return cards
