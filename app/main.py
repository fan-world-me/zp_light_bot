import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Update
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.bot.handlers import router
from app.db.database import init_db
from app.scheduler.notifier import check_and_notify

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

BOT_TOKEN      = os.environ["BOT_TOKEN"]
WEBHOOK_SECRET = os.environ["WEBHOOK_SECRET"]
WEBHOOK_HOST   = os.environ["WEBHOOK_HOST"]          # e.g. https://zp-light-bot.fly.dev
WEBHOOK_PATH   = "/webhook"
WEBHOOK_URL    = WEBHOOK_HOST + WEBHOOK_PATH

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp  = Dispatcher()
dp.include_router(router)

scheduler = AsyncIOScheduler(timezone="Europe/Kiev")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await init_db()
    log.info("DB initialised")

    await bot.set_webhook(
        url=WEBHOOK_URL,
        secret_token=WEBHOOK_SECRET,
        drop_pending_updates=True,
    )
    log.info("Webhook set: %s", WEBHOOK_URL)

    scheduler.add_job(
        check_and_notify,
        trigger="interval",
        minutes=3,
        args=[bot],
        id="notifier",
        replace_existing=True,
    )
    scheduler.start()
    log.info("Scheduler started (every 3 min)")

    yield

    # Shutdown
    scheduler.shutdown(wait=False)
    await bot.delete_webhook()
    await bot.session.close()
    log.info("Shutdown complete")


app = FastAPI(lifespan=lifespan)


@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request) -> Response:
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if secret != WEBHOOK_SECRET:
        return Response(status_code=403)

    data   = await request.json()
    update = Update.model_validate(data)
    await dp.feed_update(bot, update)
    return Response(status_code=200)


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}
