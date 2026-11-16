[![Telegram](https://img.shields.io/badge/Telegram-@fan__world__me-2CA5E0?style=flat-square&logo=telegram)](https://t.me/fan_world_me) [![GitHub](https://img.shields.io/badge/GitHub-fan--world--me-181717?style=flat-square&logo=github)](https://github.com/fan-world-me) [![Portfolio](https://img.shields.io/badge/Portfolio-fan--world--me.github.io-00e5ff?style=flat-square&logo=githubpages&logoColor=white)](https://fan-world-me.github.io/)

```
███████╗██████╗     ██╗     ██╗ ██████╗ ██╗  ██╗████████╗
╚══███╔╝██╔══██╗    ██║     ██║██╔════╝ ██║  ██║╚══██╔══╝
  ███╔╝ ██████╔╝    ██║     ██║██║  ███╗███████║   ██║
 ███╔╝  ██╔═══╝     ██║     ██║██║   ██║██╔══██║   ██║
███████╗██║         ███████╗██║╚██████╔╝██║  ██║   ██║
╚══════╝╚═╝         ╚══════╝╚═╝ ╚═════╝ ╚═╝  ╚═╝   ╚═╝
  ZP Light Bot v2.0 — Python + aiogram + Fly.io
```

Telegram-бот для відстеження графіків відключень світла у Запорізькій області. Парсить офіційний сайт [zoe.com.ua](https://www.zoe.com.ua/outage/), зберігає вибрані черги і надсилає сповіщення щойно графік оновлюється.

---

### ✨ Функції

- 🔔 **Автосповіщення** — повідомлення одразу після оновлення графіку
- 📋 **Вибір черги** — інлайн-кнопки, можна додати кілька черг
- 🗓 **Всі черги** — перегляд повного графіку одним дотиком
- 🌅 **На завтра** — графік на наступний день, щойно оператор його опублікує
- ⚙️ **Налаштування** — додати/видалити черги, перемкнути режим (тільки свої / всі)
- ⚠️ **ГАВ/СГАВ** — попередження про аварійні відключення поза графіком
- 💾 **SQLite + aiosqlite** — зберігання налаштувань на постійному томі Fly.io
- ⏱ **Планувальник кожні 3 хв** — APScheduler перевіряє оновлення автоматично

---

### 🧰 Tech Stack

**Runtime** [![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=flat-square&logo=python&logoColor=white)](#) [![Fly.io](https://img.shields.io/badge/Fly.io-deployed-7c3aed?style=flat-square&logo=flydotio&logoColor=white)](#)

**Bot framework** [![aiogram](https://img.shields.io/badge/aiogram-3.x-2CA5E0?style=flat-square&logo=telegram&logoColor=white)](#)

**Web server** [![FastAPI](https://img.shields.io/badge/FastAPI-uvicorn-009688?style=flat-square&logo=fastapi&logoColor=white)](#)

**Storage** [![SQLite](https://img.shields.io/badge/SQLite-aiosqlite-003B57?style=flat-square&logo=sqlite&logoColor=white)](#)

**Scheduler** [![APScheduler](https://img.shields.io/badge/APScheduler-3.x-ff6b35?style=flat-square)](#)

**Data source** [![zoe.com.ua](https://img.shields.io/badge/zoe.com.ua-via_OE_OUTAGE_DATA-00b894?style=flat-square)](https://github.com/yaroslav2901/OE_OUTAGE_DATA) [![alerts.energy](https://img.shields.io/badge/alerts.energy-fallback-ff6b35?style=flat-square)](https://alerts.energy/zaporizka-oblast)

---

### 🗂️ Project Structure

```
zp_light_bot/
├── app/
│   ├── main.py               — FastAPI app: webhook + scheduler startup
│   ├── bot/
│   │   ├── handlers.py       — message & callback_query handlers
│   │   └── keyboards.py      — inline keyboard builders
│   ├── db/
│   │   └── database.py       — SQLite schema, user CRUD, schedule state
│   ├── parser/
│   │   ├── base.py           — Schedule model, timezone, month names
│   │   ├── schedule.py       — fetch facade + status/format helpers
│   │   └── providers/
│   │       ├── svitlo_zp.py  — PRIMARY: svitlo.zp.ua Google Sheets CSV (today+tomorrow)
│   │       ├── oe_outage.py  — FALLBACK 1: OE_OUTAGE_DATA JSON (GitHub raw)
│   │       └── alerts_energy.py — FALLBACK 2: alerts.energy HTML scraper
│   └── scheduler/
│       └── notifier.py       — notification logic (runs every 3 min)
├── Dockerfile
├── fly.toml
├── requirements.txt
├── .gitignore
├── README.md
└── LICENSE
```

---

### 🚀 How it works

```
[Telegram user] ──► /start ──► choose queue via inline buttons
                                        │
                                        ▼
                              [saved to SQLite on /data volume]
                                        │
                    ┌───────────────────┘
                    │  APScheduler every 3 min
                    ▼
         [fetch zoe.com.ua/outage/]
                    │
         [parse queue schedules]
                    │
         [compare with last saved state]
                    │
         [schedule updated? → send notification]
```

---

### ⚙️ Deploy on Fly.io

#### 1. Створити Telegram бота

Пишемо [@BotFather](https://t.me/BotFather), отримуємо токен.

#### 2. Встановити flyctl

```bash
# macOS / Linux
curl -L https://fly.io/install.sh | sh

# Windows (PowerShell)
iwr https://fly.io/install.ps1 -useb | iex
```

#### 3. Залогінитись та створити застосунок

```bash
fly auth login
fly launch --no-deploy
```

#### 4. Встановити секрети

```bash
fly secrets set BOT_TOKEN=your_token_here
fly secrets set WEBHOOK_SECRET=any_random_string
```

#### 5. Створити постійний том

```bash
fly volumes create zp_bot_data --size 1 --region fra
```

#### 6. Задеплоїти

```bash
fly deploy
```

#### 7. Готово

Відкрий бота в Telegram і натисни **Start**.

---

### 🔁 Local development

```bash
cp .env.example .env   # заповни BOT_TOKEN, WEBHOOK_SECRET, BASE_URL
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Для тестування webhook використай [ngrok](https://ngrok.com):

```bash
ngrok http 8000
# встав ngrok URL в .env як BASE_URL
```

---

### 💛 Підтримати автора

Якщо бот корисний — можна підтримати розробку:

**Monobank:** `4441 1110 0821 0555`

---

### 📄 License

[GPL-3.0](LICENSE) © [fan-world-me](https://github.com/fan-world-me)

---

Made with 🩵 in Ukraine 🇺🇦

[![Telegram](https://img.shields.io/badge/Telegram-@fan__world__me-2CA5E0?style=flat-square&logo=telegram)](https://t.me/fan_world_me) [![Portfolio](https://img.shields.io/badge/Portfolio-fan--world--me.github.io-00e5ff?style=flat-square&logo=githubpages&logoColor=white)](https://fan-world-me.github.io/)
