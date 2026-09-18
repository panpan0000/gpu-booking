from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import select

from .db import active_reservations, get_session
from .models import Cluster, GpuActual, Machine, UsageLog

router = APIRouter(prefix="/api")


class MachineIn(BaseModel):
    name: str
    cluster_name: Optional[str] = None
    node_name: str = ""
    total_gpus: int = 8


class ClusterIn(BaseModel):
    name: str
    adapter_type: str = "custom_http"
    endpoint: str = ""
    token_env: str = ""
    extra_config: str = "{}"
    enabled: bool = True


@router.post("/clusters")
def create_cluster(c: ClusterIn):
    with get_session() as session:
        if session.exec(select(Cluster).where(Cluster.name == c.name)).first():
            raise HTTPException(409, "集群已存在")
        cluster = Cluster(**c.model_dump())
        session.add(cluster)
        session.commit()
        session.refresh(cluster)
        return cluster


@router.post("/machines")
def create_machine(m: MachineIn):
    with get_session() as session:
        if session.exec(select(Machine).where(Machine.name == m.name)).first():
            raise HTTPException(409, "机器已存在")
        cluster_id = None
        if m.cluster_name:
            cluster = session.exec(select(Cluster).where(Cluster.name == m.cluster_name)).first()
            if not cluster:
                raise HTTPException(404, f"集群 {m.cluster_name} 不存在")
            cluster_id = cluster.id
        machine = Machine(name=m.name, cluster_id=cluster_id,
                          node_name=m.node_name, total_gpus=m.total_gpus)
        session.add(machine)
        session.commit()
        session.refresh(machine)
        return machine


@router.get("/machines")
def list_machines():
    with get_session() as session:
        return list(session.exec(select(Machine)).all())


@router.get("/status")
def status():
    now = datetime.utcnow()
    with get_session() as session:
        machines = list(session.exec(select(Machine)).all())
        clusters = {c.id: c for c in session.exec(select(Cluster)).all()}
        actual_rows = list(session.exec(select(GpuActual)).all())
        result = []
        for m in machines:
            cluster = clusters.get(m.cluster_id)
            actuals = [a for a in actual_rows
                       if cluster and a.cluster_name == cluster.name
                       and a.node_name == m.node_name]
            claims = [{
                "user": r.user_name,
                "count": r.gpu_count,
                "end_at": r.end_at.isoformat(),
            } for r in active_reservations(session, m.id)]
            used = sum(c["count"] for c in claims)
            result.append({
                "machine": m.name,
                "cluster": cluster.name if cluster else "",
                "node": m.node_name,
                "total_gpus": m.total_gpus,
                "claims": claims,
                "free": m.total_gpus - used,
                "actual": [{
                    "index": a.gpu_index,
                    "allocated": a.allocated,
                    "util": a.util,
                    "mem": f"{a.mem_used}/{a.mem_total}" if a.mem_total else None,
                    "stale": (now - a.updated_at).total_seconds() > 180,
                } for a in actuals if a.gpu_index is not None],
                "node_level_occupied": sum(1 for a in actuals
                                           if a.gpu_index is None and a.allocated),
                "collected": bool(actuals),
            })
        return result


@router.get("/stats")
def stats():
    with get_session() as session:
        rows = list(session.exec(select(UsageLog)).all())
    by_user: dict[str, dict] = {}
    by_machine: dict[str, dict] = {}
    for r in rows:
        u = by_user.setdefault(r.user_name, {"user": r.user_name, "count": 0, "minutes": 0})
        u["count"] += 1
        u["minutes"] += r.minutes * r.gpu_count
        m = by_machine.setdefault(r.machine_name, {"machine": r.machine_name, "count": 0, "minutes": 0})
        m["count"] += 1
        m["minutes"] += r.minutes * r.gpu_count
    return {
        "by_user": sorted(by_user.values(), key=lambda x: -x["minutes"]),
        "by_machine": sorted(by_machine.values(), key=lambda x: -x["minutes"]),
        "total_logs": len(rows),
    }
