import json
import logging
import os
import re
import time
from typing import Optional

import httpx

log = logging.getLogger("feishu")

APP_ID = os.getenv("FEISHU_APP_ID", "")
APP_SECRET = os.getenv("FEISHU_APP_SECRET", "")
VERIFY_TOKEN = os.getenv("FEISHU_VERIFY_TOKEN", "")
BASE = "https://open.feishu.cn/open-apis"

_token_cache: dict = {"token": "", "expires_at": 0.0}


def enabled() -> bool:
    return bool(APP_ID and APP_SECRET)


async def tenant_token() -> str:
    if _token_cache["token"] and time.time() < _token_cache["expires_at"] - 60:
        return _token_cache["token"]
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(f"{BASE}/auth/v3/tenant_access_token/internal",
                                 json={"app_id": APP_ID, "app_secret": APP_SECRET})
        data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"获取 tenant_access_token 失败: {data}")
    _token_cache["token"] = data["tenant_access_token"]
    _token_cache["expires_at"] = time.time() + data.get("expire", 7200)
    return _token_cache["token"]


async def send_text(open_id: str, text: str) -> None:
    if not enabled():
        log.info("[feishu disabled] 私信 %s: %s", open_id, text)
        return
    token = await tenant_token()
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"{BASE}/im/v1/messages?receive_id_type=open_id",
            headers={"Authorization": f"Bearer {token}"},
            json={"receive_id": open_id, "msg_type": "text",
                  "content": json.dumps({"text": text})},
        )
        data = resp.json()
    if data.get("code") != 0:
        log.warning("私信发送失败 %s: %s", open_id, data)


async def reply_text(message_id: str, text: str) -> None:
    if not enabled():
        log.info("[feishu disabled] 回复 %s: %s", message_id, text)
        return
    token = await tenant_token()
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"{BASE}/im/v1/messages/{message_id}/reply",
            headers={"Authorization": f"Bearer {token}"},
            json={"msg_type": "text", "content": json.dumps({"text": text})},
        )
        data = resp.json()
    if data.get("code") != 0:
        log.warning("回复失败 %s: %s", message_id, data)


_name_cache: dict[str, str] = {}


async def user_name(open_id: str) -> str:
    if open_id in _name_cache:
        return _name_cache[open_id]
    name = open_id[:12]
    if enabled():
        try:
            token = await tenant_token()
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{BASE}/contact/v3/users/{open_id}",
                                        headers={"Authorization": f"Bearer {token}"})
                data = resp.json()
            if data.get("code") == 0:
                name = data["data"]["user"].get("name", name)
        except Exception as e:
            log.warning("获取用户名失败 %s: %s", open_id, e)
    _name_cache[open_id] = name
    return name


def extract_text(event: dict) -> tuple[Optional[str], Optional[str], Optional[str], str]:
    """从 im.message.receive_v1 事件提取 (message_id, chat_type, open_id, text)。
    群聊未 @机器人 时返回 text 为空串。"""
    try:
        msg = event["event"]["message"]
        sender = event["event"]["sender"]["sender_id"]
        open_id = sender.get("open_id", "")
        chat_type = msg.get("chat_type", "")
        message_id = msg.get("message_id", "")
        if msg.get("message_type") != "text":
            return None, None, None, ""
        content = json.loads(msg.get("content", "{}"))
        text = content.get("text", "")
    except (KeyError, json.JSONDecodeError):
        return None, None, None, ""

    # 群聊: 必须 @机器人, 并去掉 @_user_x 占位
    if chat_type == "group":
        mentions = msg.get("mentions") or []
        if not mentions:
            return None, None, None, ""
        text = re.sub(r"@_user_\d+", "", text).strip()
    return message_id, chat_type, open_id, text
