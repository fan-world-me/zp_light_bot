[![Telegram](https://img.shields.io/badge/Telegram-@fan__world__me-2CA5E0?style=flat-square&logo=telegram)](https://t.me/fan_world_me) [![GitHub](https://img.shields.io/badge/GitHub-fan--world--me-181717?style=flat-square&logo=github)](https://github.com/fan-world-me) [![Portfolio](https://img.shields.io/badge/Portfolio-fan--world--me.github.io-00e5ff?style=flat-square&logo=githubpages&logoColor=white)](https://fan-world-me.github.io/)

```
███████╗██████╗     ██╗     ██╗ ██████╗ ██╗  ██╗████████╗
╚══███╔╝██╔══██╗    ██║     ██║██╔════╝ ██║  ██║╚══██╔══╝
  ███╔╝ ██████╔╝    ██║     ██║██║  ███╗███████║   ██║
 ███╔╝  ██╔═══╝     ██║     ██║██║   ██║██╔══██║   ██║
███████╗██║         ███████╗██║╚██████╔╝██║  ██║   ██║
╚══════╝╚═╝         ╚══════╝╚═╝ ╚═════╝ ╚═╝  ╚═╝   ╚═╝
  ZP Light Bot v2.1 — Python + aiogram + Northflank + Turso
```

Telegram-бот для відстеження графіків відключень світла у Запорізькій області. Бере графік із [svitlo.zp.ua](https://svitlo.zp.ua/) (волонтерські Google-таблиці з даними [Запоріжжяобленерго](https://www.zoe.com.ua/outage/)), зберігає вибрані черги і надсилає сповіщення щойно графік оновлюється.

---

### ✨ Функції

- 🔔 **Автосповіщення** — повідомлення одразу після оновлення графіка
- 📋 **Вибір черги** — інлайн-кнопки, можна додати кілька черг
- 🗓 **Всі черги** — перегляд повного графіка одним дотиком
- 🌅 **На завтра** — графік на наступний день, щойно оператор його опублікує
- ⚙️ **Налаштування** — додати/видалити черги, перемкнути режим (тільки свої / всі)
- ⚡ **Миттєва відповідь** — кнопка одразу показує графік із кешу, а якщо дані тим часом оновилися, надсилає нове повідомлення
- 💾 **Turso (libSQL)** — налаштування користувачів у хмарній SQLite-базі, не залежать від диска сервера
- ⏱ **Планувальник кожні 3 хв** — APScheduler перевіряє оновлення автоматично

---

### 🧰 Tech Stack

**Runtime** [![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=flat-square&logo=python&logoColor=white)](#) [![Northflank](https://img.shields.io/badge/Northflank-deployed-0a0a0a?style=flat-square)](#)

**Bot framework** [![aiogram](https://img.shields.io/badge/aiogram-3.x-2CA5E0?style=flat-square&logo=telegram&logoColor=white)](#)

**Web server** [![FastAPI](https://img.shields.io/badge/FastAPI-uvicorn-009688?style=flat-square&logo=fastapi&logoColor=white)](#)

**Storage** [![Turso](https://img.shields.io/badge/Turso-libSQL-4ff8d2?style=flat-square)](https://turso.tech/)

**Scheduler** [![APScheduler](https://img.shields.io/badge/APScheduler-3.x-ff6b35?style=flat-square)](#)

**Data source** [![svitlo.zp.ua](https://img.shields.io/badge/svitlo.zp.ua-Google_Sheets-00b894?style=flat-square)](https://svitlo.zp.ua/)

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
│   │   └── database.py       — Turso: schema, user CRUD (з кешем у пам'яті), знімки графіка
│   ├── parser/
│   │   ├── base.py           — Schedule model, timezone, month names
│   │   ├── schedule.py       — кеш графіка (stale-while-revalidate) + форматування
│   │   └── providers/
│   │       └── svitlo_zp.py  — svitlo.zp.ua: gviz-запит лише на сьогодні/завтра
│   └── scheduler/
│       └── notifier.py       — логіка сповіщень (кожні 3 хв)
├── Dockerfile
├── requirements.txt
├── .env.example
└── LICENSE
```

---

### 🚀 How it works

```
[Telegram user] ──► кнопка ──► відповідь одразу з кешу в пам'яті
                                   │
                                   └─► у фоні одне оновлення на всіх
                                            │  змінилося? → нове повідомлення

[APScheduler, кожні 3 хв]
        │
[svitlo.zp.ua: авто-таблиця → напівавто → проксі]
        │
[порівняння зі знімком у Turso]
        │
[графік змінився? → сповіщення користувачам]
```

---

### ⚙️ Deploy: Northflank + Turso

#### 1. Створити Telegram бота

Пишемо [@BotFather](https://t.me/BotFather), отримуємо токен.

#### 2. База даних Turso

Створи базу на [turso.tech](https://turso.tech/) і токен доступу до неї. Знадобляться:

- `TURSO_DATABASE_URL` — адреса виду `libsql://<db>-<org>.<region>.turso.io`
- `TURSO_AUTH_TOKEN` — токен бази

Таблиці бот створює сам при старті.

#### 3. Сервіс у Northflank

1. Create service → **Combined service**, джерело — цей репозиторій, гілка `master`, збірка за `Dockerfile`.
2. Порт: `8000`, протокол HTTP, **публічний**.
3. Health check: HTTP `GET /healthz` на порту `8000`.
4. Ресурси: план з 512 МБ пам'яті (256 МБ — на межі).
5. Змінні оточення (**тільки тут, не в Git**):

| Змінна               | Значення                                                      |
| -------------------- | ------------------------------------------------------------- |
| `BOT_TOKEN`          | токен від BotFather                                           |
| `WEBHOOK_SECRET`     | довільний випадковий рядок                                    |
| `WEBHOOK_HOST`       | публічна адреса сервісу (`https://…code.run`, без `/` в кінці) |
| `TURSO_DATABASE_URL` | адреса бази Turso                                             |
| `TURSO_AUTH_TOKEN`   | токен бази Turso                                              |

Northflank автоматично збирає і деплоїть кожен коміт у `master`. Webhook бот реєструє сам при старті.

#### 4. Готово

Відкрий бота в Telegram і натисни **Start**.

---

### 🔁 Local development

```bash
cp .env.example .env   # заповни BOT_TOKEN, WEBHOOK_SECRET, WEBHOOK_HOST, TURSO_*
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Для локального webhook потрібна публічна HTTPS-адреса (наприклад, через `cloudflared tunnel --url http://localhost:8000`) — її значення йде в `WEBHOOK_HOST`.

---

### 📄 License

GPL-3.0 — див. [LICENSE](LICENSE).
