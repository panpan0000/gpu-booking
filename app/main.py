import logging
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .api import router as api_router
from .db import init_db
from .feishu import VERIFY_TOKEN, extract_text, reply_text, send_text
from .handler import handle_message
from .scheduler import start_scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("main")

templates = Jinja2Templates(directory="app/templates")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    start_scheduler()
    yield


app = FastAPI(title="GPU 预约", lifespan=lifespan)
app.include_router(api_router)


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.get("/stats", response_class=HTMLResponse)
def stats_page(request: Request):
    return templates.TemplateResponse(request, "stats.html")


@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request):
    return templates.TemplateResponse(request, "admin.html")


@app.post("/feishu/events")
async def feishu_events(request: Request, background: BackgroundTasks):
    body = await request.json()

    # URL 验证
    if body.get("type") == "url_verification":
        if VERIFY_TOKEN and body.get("token") != VERIFY_TOKEN:
            return {"code": 403}
        return {"challenge": body.get("challenge")}

    header = body.get("header", {})
    if VERIFY_TOKEN and header.get("token") and header.get("token") != VERIFY_TOKEN:
        return {"code": 403}

    if header.get("event_type") == "im.message.receive_v1":
        message_id, chat_type, open_id, text = extract_text(body)
        if text and open_id:
            background.add_task(_process, message_id, open_id, text)
    # 先返回 200, 业务异步处理
    return {"code": 0}


async def _process(message_id: str, open_id: str, text: str) -> None:
    try:
        reply = await handle_message(open_id, text)
    except Exception as e:
        log.exception("处理消息失败")
        reply = f"出错了: {e}"
    if message_id:
        await reply_text(message_id, reply)
    else:
        await send_text(open_id, reply)
