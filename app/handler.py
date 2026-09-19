import logging
from datetime import datetime
from typing import Optional

from sqlmodel import select

from .db import (active_reservations, book, book_in_cluster, get_session,
                 release, renew)
from .models import Cluster, Machine, Reservation
from .parser import Command, parse_message

log = logging.getLogger("handler")

HELP = """用法(群里 @我):
  申请:  @我 集群一 4卡 4h            自动选节点
         @我 集群一 节点5 4卡 4h      指定节点
         @我 集群一 节点5~10 4卡 4h   节点范围
  查询:  @我 状态 / @我 我的
私信我:
  续 2h            续订(多条时: 续 gpu-node-05 2h)
  释放             提前释放(多条时: 释放 gpu-node-05)"""


async def handle_message(open_id: str, text: str) -> str:
    cmd = parse_message(text)
    if cmd.action == "help":
        return HELP
    if cmd.action == "unknown":
        return f"没看懂「{text}」。\n{HELP}"

    from .feishu import user_name
    name = await user_name(open_id)

    if cmd.action == "status":
        return _status_text()
    if cmd.action == "mine":
        return _mine_text(open_id)
    if cmd.action == "book":
        return _book(cmd, open_id, name)
    if cmd.action == "release":
        return _release(cmd, open_id)
    if cmd.action == "renew":
        if cmd.hours <= 0:
            return "续订时长不对, 例如: 续 2h"
        return _renew(cmd, open_id)
    return HELP


def _resolve_mine(s, cmd: Command, open_id: str) -> tuple[Optional[Reservation], str]:
    """定位用户要操作的申请: 按单号 > 按节点/机器名 > 唯一一条。多条时让用户选。"""
    if cmd.reservation_id is not None:
        r = s.get(Reservation, cmd.reservation_id)
        if not r or r.status != "active":
            return None, "申请不存在或已结束"
        if r.user_open_id != open_id:
            return None, "这不是你的申请"
        return r, ""
    mine = [r for r in active_reservations(s) if r.user_open_id == open_id]
    if cmd.machine_name:
        clusters = {c.id: c.name for c in s.exec(select(Cluster)).all()}
        target = cmd.machine_name.replace("/", " ").strip()

        def match(r) -> bool:
            m = s.get(Machine, r.machine_id)
            if not m:
                return False
            cname = clusters.get(m.cluster_id, "")
            label = m.node_name or m.name
            forms = {m.name, label, f"{cname} {label}".strip(),
                     f"{cname}/{label}".strip("/"), f"{cname} {m.name}".strip()}
            return target in forms or cmd.machine_name in forms

        mine = [r for r in mine if match(r)]
    if not mine:
        return None, "没有匹配的进行中的占用。发「我的」查看"
    if len(mine) > 1:
        # 同一节点有多条: 直接操作最早到期的那条, 不再让用户选
        if len({r.machine_id for r in mine}) == 1:
            r = min(mine, key=lambda x: x.end_at)
            m = s.get(Machine, r.machine_id)
            return r, f"(提示: 你在 {m.node_name or m.name} 有 {len(mine)} 条占用, 操作的是最早到期的一条)"
        lines = []
        clusters = {c.id: c.name for c in s.exec(select(Cluster)).all()}
        for r in mine:
            m = s.get(Machine, r.machine_id)
            label = m.node_name or m.name if m else str(r.machine_id)
            cname = clusters.get(m.cluster_id, "") if m else ""
            lines.append(f"  {cname} {label} {r.gpu_count}卡 到 {r.end_at:%m-%d %H:%M}".strip())
        first_m = s.get(Machine, mine[0].machine_id)
        first_label = f"{clusters.get(first_m.cluster_id, '')} {first_m.node_name or first_m.name}".strip()
        example = f"释放 {first_label}" if cmd.action == "release" else f"续 {first_label} 2h"
        return None, ("你有多条占用, 指定节点名再操作:\n" + "\n".join(lines)
                      + f"\n例如: {example}")
    return mine[0], ""


def _release(cmd: Command, open_id: str) -> str:
    with get_session() as s:
        r, note = _resolve_mine(s, cmd, open_id)
        if not r:
            return note
        ok, msg = release(s, r.id, open_id)
        if ok:
            m = s.get(Machine, r.machine_id)
            return f"{note}\n已释放 {m.node_name or m.name} {r.gpu_count}卡".strip()
        return msg


def _renew(cmd: Command, open_id: str) -> str:
    with get_session() as s:
        r, note = _resolve_mine(s, cmd, open_id)
        if not r:
            return note
        ok, msg = renew(s, r.id, open_id, cmd.hours)
        if ok:
            m = s.get(Machine, r.machine_id)
            return f"{note}\n已续订 {m.node_name or m.name} {r.gpu_count}卡 到 {r.end_at:%m-%d %H:%M}".strip()
        return msg


def _book(cmd: Command, open_id: str, name: str) -> str:
    if cmd.hours <= 0:
        return "时长不对, 例如: 集群一 4卡 4h"
    if not cmd.gpu_count:
        return "没看懂要几张卡, 例如: 集群一 4卡 4h"
    with get_session() as s:
        # 省略集群名: 只有一个集群时默认用它
        if not cmd.machine_name:
            all_clusters = list(s.exec(select(Cluster)).all())
            if len(all_clusters) == 1:
                cluster = all_clusters[0]
                r, err = book_in_cluster(s, cluster, "", open_id, name,
                                         cmd.hours, cmd.gpu_count)
                if not r:
                    return f"申请失败: {err}"
                m = s.get(Machine, r.machine_id)
                return (f"申请成功 ✅\n集群 {cluster.name} 节点 {m.node_name or m.name}, "
                        f"{r.gpu_count}卡, 到 {r.end_at:%m-%d %H:%M} 到期。到期前我会私信你续订。")
            names = ", ".join(c.name for c in all_clusters) or "(无)"
            return f"请指定集群, 例如: {all_clusters[0].name if all_clusters else '集群名'} 4卡 4h。现有集群: {names}"
        # 优先按集群名解析; "集群一" 本身可能就是集群名, 也尝试去前缀
        targets = [cmd.machine_name]
        for prefix in ("集群", "机器"):
            if cmd.machine_name.startswith(prefix):
                targets.append(cmd.machine_name[len(prefix):])
        cluster = machine = None
        for t in targets:
            cluster = s.exec(select(Cluster).where(Cluster.name == t)).first()
            if cluster:
                break
        if not cluster:
            for t in targets:
                machine = s.exec(select(Machine).where(Machine.name == t)).first()
                if machine:
                    break
        if cluster:
            r, err = book_in_cluster(s, cluster, cmd.node_spec, open_id, name,
                                     cmd.hours, cmd.gpu_count)
            if not r:
                return f"申请失败: {err}"
            m = s.get(Machine, r.machine_id)
            return (f"申请成功 ✅\n集群 {cluster.name} 节点 {m.node_name or m.name}, "
                    f"{r.gpu_count}卡, 到 {r.end_at:%m-%d %H:%M} 到期。到期前我会私信你续订。")
        if cmd.node_spec:
            return f"「{cmd.machine_name}」不是集群名, 节点指定只对集群有效"
        if not machine:
            clusters = [c.name for c in s.exec(select(Cluster)).all()]
            names = [m.name for m in s.exec(select(Machine)).all()]
            return (f"没有集群或机器「{cmd.machine_name}」。\n"
                    f"集群: {', '.join(clusters) or '(无)'}\n机器: {', '.join(names) or '(无)'}")
        r, err = book(s, machine, open_id, name, cmd.hours, cmd.gpu_count)
        if not r:
            return f"申请失败: {err}"
        return (f"申请成功 ✅\n机器 {machine.name}, {r.gpu_count}卡, "
                f"到 {r.end_at:%m-%d %H:%M} 到期。到期前我会私信你续订。")


def _status_text() -> str:
    lines = []
    with get_session() as s:
        clusters = {c.id: c.name for c in s.exec(select(Cluster)).all()}
        machines = list(s.exec(select(Machine)).all())
        machines.sort(key=lambda m: (clusters.get(m.cluster_id, ""), m.node_name or m.name))
        last_cluster = None
        for m in machines:
            cname = clusters.get(m.cluster_id, "未分组")
            if cname != last_cluster:
                lines.append(f"== {cname} ==")
                last_cluster = cname
            rs = active_reservations(s, m.id)
            used = sum(r.gpu_count for r in rs)
            label = m.node_name or m.name
            lines.append(f"【{label}】空闲 {m.total_gpus - used}/{m.total_gpus}")
            for r in sorted(rs, key=lambda x: -x.gpu_count):
                left = int((r.end_at - datetime.now()).total_seconds() // 60)
                lines.append(f"  {r.gpu_count}卡: {r.user_name} 剩{left}分钟")
    return "\n".join(lines) or "还没有登记任何机器"


def _mine_text(open_id: str) -> str:
    with get_session() as s:
        rs = [r for r in active_reservations(s) if r.user_open_id == open_id]
        if not rs:
            return "你当前没有进行中的占用"
        lines = []
        for r in rs:
            m = s.get(Machine, r.machine_id)
            label = f"{m.node_name or m.name}" if m else str(r.machine_id)
            lines.append(f"{label} {r.gpu_count}卡 到 {r.end_at:%m-%d %H:%M}")
        return "\n".join(lines)
