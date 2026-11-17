import asyncio
import hmac
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

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

scheduler = AsyncIOScheduler(timezone="Europe/Kyiv")

# Сильні посилання на фонові задачі, щоб їх не з'їв GC до завершення
_tasks: set[asyncio.Task] = set()


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
        # За замовчуванням grace = 1 сек, і запуск, що запізнився на 3 сек, просто скасовувався
        misfire_grace_time=120,
        coalesce=True,
        max_instances=1,
        next_run_time=datetime.now(scheduler.timezone) + timedelta(seconds=20),
    )
    scheduler.start()
    log.info("Scheduler started (every 3 min)")

    yield

    # Shutdown
    scheduler.shutdown(wait=False)
    # webhook навмисно НЕ видаляємо: при рестарті/деплої нова машина ставить його сама,
    # а delete тут міг прилетіти вже після set_webhook нового інстансу
    await bot.session.close()
    log.info("Shutdown complete")


app = FastAPI(lifespan=lifespan)


async def _process_update(update: Update) -> None:
    try:
        await dp.feed_update(bot, update)
    except Exception:
        log.exception("Update processing failed")


@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request) -> Response:
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not hmac.compare_digest(secret.encode(), WEBHOOK_SECRET.encode()):
        return Response(status_code=403)

    try:
        update = Update.model_validate(await request.json())
    except Exception:
        return Response(status_code=400)

    # Відповідаємо Telegram одразу, а апдейт обробляємо у фоні:
    # інакше довгий фетч графіка → таймаут вебхука → повторна доставка → дублі відповідей
    task = asyncio.create_task(_process_update(update))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return Response(status_code=200)


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}
