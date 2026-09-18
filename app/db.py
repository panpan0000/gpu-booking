import os
from datetime import datetime, timedelta
from typing import Optional

from sqlmodel import Session, SQLModel, create_engine, select

from .models import Cluster, Machine, Reservation, UsageLog
from .parser import match_node

DB_URL = os.getenv("DB_URL", "sqlite:///gpu_booking.db")
engine = create_engine(DB_URL, connect_args={"check_same_thread": False})


def init_db() -> None:
    SQLModel.metadata.create_all(engine)


def get_session() -> Session:
    return Session(engine)


def active_reservations(session: Session, machine_id: Optional[int] = None) -> list[Reservation]:
    q = select(Reservation).where(Reservation.status == "active", Reservation.end_at > datetime.utcnow())
    if machine_id is not None:
        q = q.where(Reservation.machine_id == machine_id)
    return list(session.exec(q).all())


def used_count(session: Session, machine_id: int) -> int:
    return sum(r.gpu_count for r in active_reservations(session, machine_id))


def _create(session: Session, machine: Machine, user_open_id: str, user_name: str,
            hours: float, gpu_count: int) -> Reservation:
    now = datetime.utcnow()
    r = Reservation(
        machine_id=machine.id,
        gpu_count=gpu_count,
        user_open_id=user_open_id,
        user_name=user_name,
        start_at=now,
        end_at=now + timedelta(hours=hours),
    )
    session.add(r)
    session.commit()
    session.refresh(r)
    return r


def book(session: Session, machine: Machine, user_open_id: str, user_name: str,
         hours: float, gpu_count: int) -> tuple[Optional[Reservation], str]:
    if gpu_count < 1:
        return None, "数量至少是 1 张"
    free = machine.total_gpus - used_count(session, machine.id)
    if free < gpu_count:
        return None, f"{machine.name} 空闲卡不足: 需要 {gpu_count} 张, 只剩 {free} 张"
    return _create(session, machine, user_open_id, user_name, hours, gpu_count), ""


def book_in_cluster(session: Session, cluster: Cluster, node_spec: str,
                    user_open_id: str, user_name: str, hours: float,
                    gpu_count: int) -> tuple[Optional[Reservation], str]:
    """集群内申请: 按 node_spec 过滤节点, 选空闲最多的节点。"""
    machines = list(session.exec(
        select(Machine).where(Machine.cluster_id == cluster.id)).all())
    candidates = [(m, m.total_gpus - used_count(session, m.id))
                  for m in machines if match_node(m.node_name or m.name, node_spec)]
    if not candidates:
        return None, (f"集群 {cluster.name} 下没有匹配「节点{node_spec}」的机器"
                      if node_spec else f"集群 {cluster.name} 下没有登记机器")
    candidates.sort(key=lambda x: -x[1])
    machine, free = candidates[0]
    if free < gpu_count:
        avail = ", ".join(f"{m.node_name or m.name}(空闲{f})" for m, f in candidates)
        return None, f"没有节点能满足 {gpu_count} 张卡。当前: {avail}"
    return _create(session, machine, user_open_id, user_name, hours, gpu_count), ""


def _close(session: Session, r: Reservation, status: str, closed_by: str) -> None:
    r.status = status
    session.add(r)
    minutes = max(1, int((r.end_at - r.start_at).total_seconds() // 60))
    machine = session.get(Machine, r.machine_id)
    session.add(UsageLog(
        reservation_id=r.id,
        machine_id=r.machine_id,
        machine_name=machine.name if machine else "",
        gpu_count=r.gpu_count,
        user_open_id=r.user_open_id,
        user_name=r.user_name,
        start_at=r.start_at,
        end_at=r.end_at,
        minutes=minutes,
        closed_by=closed_by,
    ))
    session.commit()


def release(session: Session, reservation_id: int, user_open_id: str) -> tuple[bool, str]:
    r = session.get(Reservation, reservation_id)
    if not r or r.status != "active":
        return False, "申请不存在或已结束"
    if r.user_open_id != user_open_id:
        return False, "这不是你的申请"
    r.end_at = datetime.utcnow()
    _close(session, r, "released", "released")
    return True, ""


def renew(session: Session, reservation_id: int, user_open_id: str, hours: float) -> tuple[bool, str]:
    r = session.get(Reservation, reservation_id)
    if not r or r.status != "active":
        return False, "申请不存在或已结束"
    if r.user_open_id != user_open_id:
        return False, "这不是你的申请"
    r.end_at = r.end_at + timedelta(hours=hours)
    r.reminded = False
    session.add(r)
    session.commit()
    return True, ""


def expire_due(session: Session) -> list[Reservation]:
    """到期自动释放, 返回被释放的申请列表(用于私信通知)。"""
    now = datetime.utcnow()
    due = list(session.exec(
        select(Reservation).where(Reservation.status == "active", Reservation.end_at <= now)
    ).all())
    for r in due:
        _close(session, r, "finished", "expired")
    return due


def due_for_remind(session: Session, within_minutes: int = 10) -> list[Reservation]:
    now = datetime.utcnow()
    soon = now + timedelta(minutes=within_minutes)
    rs = list(session.exec(
        select(Reservation).where(
            Reservation.status == "active",
            Reservation.reminded == False,  # noqa: E712
            Reservation.end_at > now,
            Reservation.end_at <= soon,
        )
    ).all())
    for r in rs:
        r.reminded = True
        session.add(r)
    session.commit()
    return rs
