import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Command:
    action: str  # book / status / mine / renew / release / help / unknown
    machine_name: str = ""   # 机器名或集群名, 由 handler 判定
    node_spec: str = ""      # "5" / "5~10" / "gpu-node-05", 空为不指定
    gpu_count: Optional[int] = None
    hours: float = 0.0
    reservation_id: Optional[int] = None
    raw: str = ""


_DUR_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*(d|天|h|小时|m|分钟)?$", re.IGNORECASE)


def _parse_hours(text: str) -> float:
    m = _DUR_RE.match(text.strip())
    if not m:
        return 0.0
    val = float(m.group(1))
    unit = (m.group(2) or "h").lower()
    if unit in ("d", "天"):
        return val * 24
    if unit in ("m", "分钟"):
        return val / 60
    return val


def parse_message(text: str) -> Command:
    """解析群/私信消息文本为命令。文本已去除 @机器人 占位。"""
    text = text.strip()
    cmd = Command(action="unknown", raw=text)
    if not text:
        return cmd

    if text in ("帮助", "help", "?", "？"):
        cmd.action = "help"
        return cmd
    if text in ("状态", "占用", "status"):
        cmd.action = "status"
        return cmd
    if text in ("我的", "我的预约", "mine"):
        cmd.action = "mine"
        return cmd

    # 续: "续 2h" / "续 gpu-node-05 2h" / "续 集群一 gpu-node-05 2h" / "续 R123 2h"(兼容)
    m = re.match(r"^(续|续订|renew)\s+(?:[Rr#](\d+)\s+)?(?:(\S+?)\s+)?(?:(\S+?)\s+)?(\d+(?:\.\d+)?\s*(?:d|天|h|小时|m|分钟)?)$", text, re.IGNORECASE)
    if m:
        cmd.action = "renew"
        cmd.reservation_id = int(m.group(2)) if m.group(2) else None
        # 两个名称段时: 前=集群 后=节点; 一个时段: 节点或机器名
        parts = [p for p in (m.group(3), m.group(4)) if p]
        cmd.machine_name = " ".join(parts)
        cmd.hours = _parse_hours(m.group(5))
        return cmd

    # 释放: "释放" / "释放 gpu-node-05" / "释放 集群一 gpu-node-05" / "释放 R123"(兼容)
    m = re.match(r"^(释放|退订|取消|release)(?:\s+(?:[Rr#]?(\d+)|(.+)))?\s*$", text)
    if m:
        cmd.action = "release"
        cmd.reservation_id = int(m.group(2)) if m.group(2) else None
        cmd.machine_name = (m.group(3) or "").strip()
        return cmd

    # 申请, 目标可以是集群或机器(只按数量, 不指定物理卡号):
    #   "集群一 4卡 4h"            不指定节点, 自动选
    #   "集群一 节点5 4卡 4h"      指定节点
    #   "集群一 节点5~10 4卡 4h"   节点范围
    m = re.match(
        r"^(\S+?)\s+(?:节点([\w\-\.~～到]+?)\s+)?(\d+)\s*卡?\s+(\S+)$",
        text)
    if m:
        hours = _parse_hours(m.group(4))
        if hours > 0:
            cmd.action = "book"
            # "机器A"/"集群一" 本身可能就是名字; handler 会先做全名匹配再尝试去前缀
            cmd.machine_name = m.group(1)
            cmd.node_spec = (m.group(2) or "").strip()
            cmd.gpu_count = int(m.group(3))
            cmd.hours = hours
            return cmd

    # 省略集群/节点: "4卡 4h" — 只有一个集群时由 handler 落到默认集群
    m = re.match(r"^(\d+)\s*卡\s*(\S+)$", text)
    if m:
        hours = _parse_hours(m.group(2))
        if hours > 0:
            cmd.action = "book"
            cmd.gpu_count = int(m.group(1))
            cmd.hours = hours
            return cmd

    return cmd


def match_node(node_name: str, spec: str) -> bool:
    """节点匹配: 支持精确名、尾号("5" 匹配 gpu-node-05)、范围("5~10")。"""
    if not spec:
        return True
    m = re.match(r"^(\d+)\s*(?:~|～|-|到)\s*(\d+)$", spec)
    tail = re.search(r"(\d+)\D*$", node_name)
    tail_num = int(tail.group(1)) if tail else None
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        return tail_num is not None and lo <= tail_num <= hi
    if node_name == spec:
        return True
    if spec.isdigit():
        return tail_num == int(spec)
    return False
