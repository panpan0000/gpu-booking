from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class Cluster(SQLModel, table=True):
    __tablename__ = "clusters"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)
    adapter_type: str = "custom_http"  # k8s_api / prometheus / custom_http
    endpoint: str = ""
    token_env: str = ""
    extra_config: str = "{}"  # JSON
    enabled: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Machine(SQLModel, table=True):
    __tablename__ = "machines"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)  # 业务名: 机器A
    cluster_id: Optional[int] = Field(default=None, foreign_key="clusters.id")
    node_name: str = ""  # K8s 节点名
    total_gpus: int = 8
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Reservation(SQLModel, table=True):
    __tablename__ = "reservations"

    id: Optional[int] = Field(default=None, primary_key=True)
    machine_id: int = Field(foreign_key="machines.id", index=True)
    gpu_count: int = 1
    user_open_id: str = ""
    user_name: str = ""
    start_at: datetime = Field(default_factory=datetime.utcnow)
    end_at: datetime
    status: str = Field(default="active", index=True)  # active / finished / released
    reminded: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)


class UsageLog(SQLModel, table=True):
    __tablename__ = "usage_logs"

    id: Optional[int] = Field(default=None, primary_key=True)
    reservation_id: int
    machine_id: int = Field(index=True)
    machine_name: str = ""
    gpu_count: int = 1
    user_open_id: str = Field(index=True)
    user_name: str = Field(index=True)
    start_at: datetime
    end_at: datetime
    minutes: int
    closed_by: str = ""  # expired / released
    created_at: datetime = Field(default_factory=datetime.utcnow)


class GpuActual(SQLModel, table=True):
    __tablename__ = "gpu_actual"

    id: Optional[int] = Field(default=None, primary_key=True)
    cluster_name: str = Field(index=True)
    node_name: str = Field(index=True)
    gpu_index: Optional[int] = Field(default=None)
    allocated: bool = False
    pod_name: str = ""
    namespace: str = ""
    user_name: str = ""
    util: Optional[int] = None
    mem_used: Optional[int] = None
    mem_total: Optional[int] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)
