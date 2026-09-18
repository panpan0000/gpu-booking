from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from ..models import Cluster


@dataclass
class GpuCardStatus:
    cluster_name: str
    node_name: str
    gpu_index: Optional[int] = None
    allocated: bool = False
    pod_name: str = ""
    namespace: str = ""
    user_name: str = ""
    util: Optional[int] = None
    mem_used: Optional[int] = None
    mem_total: Optional[int] = None
    updated_at: datetime = None

    def __post_init__(self):
        if self.updated_at is None:
            self.updated_at = datetime.now()


class GpuStatusAdapter:
    def __init__(self, cluster: Cluster):
        self.cluster = cluster

    async def fetch(self) -> list[GpuCardStatus]:
        raise NotImplementedError
