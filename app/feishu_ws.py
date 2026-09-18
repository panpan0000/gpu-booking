"""飞书长连接(WebSocket)模式: 纯内网部署时收消息用, 无需公网回调地址。

注意: lark ws 模块在 import 时就抓取当前事件循环, 必须在新线程里首次 import,
否则会抢到 uvicorn 正在运行的 loop 导致 "event loop is already running"。
"""
import asyncio
import json
import logging
import re
import threading

log = logging.getLogger("feishu_ws")


def start(app_id: str, app_secret: str) -> None:
    threading.Thread(target=_run, args=(app_id, app_secret),
                     daemon=True, name="feishu-ws").start()
    log.info("飞书长连接已启动")


def _run(app_id: str, app_secret: str) -> None:
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

    def _on_message(data: P2ImMessageReceiveV1) -> None:
        try:
            msg = data.event.message
            if msg.message_type != "text":
                return
            text = json.loads(msg.content).get("text", "")
            # 群聊必须 @机器人
            if msg.chat_type == "group":
                if not msg.mentions:
                    return
                text = re.sub(r"@_user_\d+", "", text).strip()
            open_id = data.event.sender.sender_id.open_id
            if not text or not open_id:
                return
            # 回调在 lark ws 的事件循环里执行, asyncio.run 需要无线程循环, 另起线程
            threading.Thread(target=lambda: asyncio.run(_process(msg.message_id, open_id, text)),
                             daemon=True).start()
        except Exception:
            log.exception("长连接事件处理失败")

    handler = (lark.EventDispatcherHandler.builder("", "")
               .register_p2_im_message_receive_v1(_on_message)
               .build())
    client = lark.ws.Client(app_id, app_secret, event_handler=handler,
                            log_level=lark.LogLevel.WARNING)
    client.start()


async def _process(message_id: str, open_id: str, text: str) -> None:
    from .feishu import reply_text
    from .handler import handle_message
    try:
        reply = await handle_message(open_id, text)
    except Exception as e:
        log.exception("处理消息失败")
        reply = f"出错了: {e}"
    await reply_text(message_id, reply)
