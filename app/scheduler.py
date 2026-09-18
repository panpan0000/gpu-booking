import asyncio
import logging
from datetime import datetime

from sqlmodel import select

from .adapters import ADAPTERS
from .db import get_session, expire_due, due_for_remind
from .feishu import send_text
from .models import Cluster, GpuActual, Machine

log = logging.getLogger("scheduler")


async def collect_all() -> None:
    with get_session() as session:
        clusters = list(session.exec(select(Cluster).where(Cluster.enabled == True)).all())  # noqa: E712
    for cluster in clusters:
        try:
            adapter_cls = ADAPTERS.get(cluster.adapter_type)
            if not adapter_cls:
                log.warning("未知适配器类型 %s (cluster %s)", cluster.adapter_type, cluster.name)
                continue
            cards = await adapter_cls(cluster).fetch()
            _upsert_cards(cluster.name, cards)
            log.info("采集 %s 完成: %d 条", cluster.name, len(cards))
        except Exception as e:
            log.warning("采集 %s 失败: %s", cluster.name, e)


def _upsert_cards(cluster_name: str, cards) -> None:
    with get_session() as session:
        old = list(session.exec(select(GpuActual).where(GpuActual.cluster_name == cluster_name)).all())
        for row in old:
            session.delete(row)
        for c in cards:
            session.add(GpuActual(
                cluster_name=c.cluster_name,
                node_name=c.node_name,
                gpu_index=c.gpu_index,
                allocated=c.allocated,
                pod_name=c.pod_name,
                namespace=c.namespace,
                user_name=c.user_name,
                util=c.util,
                mem_used=c.mem_used,
                mem_total=c.mem_total,
                updated_at=datetime.now(),
            ))
        session.commit()


async def remind_and_expire() -> None:
    with get_session() as session:
        for r in due_for_remind(session, within_minutes=10):
            machine = session.get(Machine, r.machine_id)
            name = (machine.node_name or machine.name) if machine else str(r.machine_id)
            await send_text(r.user_open_id,
                            f"提醒: 你在 {name} 的 {r.gpu_count}卡 将于 {r.end_at:%H:%M} 到期。"
                            f"回复「续 2h」续订, 或「释放」提前释放")
        for r in expire_due(session):
            machine = session.get(Machine, r.machine_id)
            name = (machine.node_name or machine.name) if machine else str(r.machine_id)
            await send_text(r.user_open_id, f"你在 {name} 的 {r.gpu_count}卡 已到期自动释放")


def start_scheduler() -> None:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    sched = AsyncIOScheduler()
    sched.add_job(remind_and_expire, "interval", minutes=1, id="remind_expire")
    sched.add_job(collect_all, "interval", seconds=60, id="collect")
    sched.start()
    # 启动即采集一次, 不阻塞启动
    asyncio.get_event_loop().create_task(collect_all())
    log.info("定时任务已启动: 提醒/过期 1min, 采集 60s")
