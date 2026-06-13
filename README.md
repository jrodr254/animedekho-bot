# AnimeDekho Telegram Bot 🎌

Telegram bot to search, browse, and download anime from [AnimeDekho](https://animedekho.app/) — Hindi, Tamil & Telugu dubbed.

Built with **Pyrogram (MTProto)** for **2GB upload/download support**.

## Features

- 🔍 **Search** — Just type any anime name to search
- 📺 **Browse Series** — Paginated recent series listing
- 🎬 **Movies** — Browse dubbed movies
- 📂 **Genres** — Filter by category
- 📂 **Seasons & Episodes** — Full navigation with inline buttons
- ▶️ **Multi-Server** — Extracts real video player URLs from multiple CDN servers
- 📥 **Downloads** — Download episodes with quality selection (360p/480p/720p/1080p)
- 📦 **Batch Download** — Download entire seasons at once
- 🔄 **Smart Server Switching** — Auto-finds quality on other servers if unavailable
- 💾 **MongoDB Storage** — Persistent user data, file cache, library
- 📢 **Main Channel Library** — All downloads saved with poster & deep links
- 🔐 **User Management** — Owner-controlled access via /adduser
- 🚫 **Duplicate Prevention** — Cached files served instantly, no re-download
- ⚡ **Fast** — Async with response caching

## Project Structure

```
├── main.py                 # Entrypoint
├── config/settings.py      # All configuration
├── api/
│   ├── models.py           # Data models
│   ├── parser.py           # HTML parsers
│   └── client.py           # API client
├── extractors/
│   ├── resolver.py         # CDN player extractors + m3u8 quality parser
│   └── shortener.py        # Link shortener bypass (gplinks, vshort, cuty)
├── bot/
│   ├── app.py              # Pyrogram app factory
│   ├── auth.py             # User authorization
│   ├── database.py         # MongoDB integration
│   ├── downloader.py       # Download manager (ffmpeg + MTProto upload)
│   ├── forcesub.py         # Force subscribe checker
│   ├── keyboards.py        # Inline keyboard builders
│   ├── library.py          # Main channel library manager
│   ├── logger.py           # Log channel integration
│   └── handlers/           # Command & callback handlers
└── utils/
    ├── cache.py            # TTL cache
    ├── http.py             # HTTP client with caching
    └── helpers.py          # Utilities
```

## Railway Deployment

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/new/template)

### Quick Deploy

1. Fork this repo
2. Create a new project on [Railway](https://railway.app)
3. Connect your GitHub repo
4. Add a **MongoDB** service (Railway has one-click MongoDB)
5. Set environment variables (see below)
6. Deploy!

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `BOT_TOKEN` | ✅ | Telegram Bot API token from @BotFather |
| `API_ID` | ✅ | Telegram API ID from my.telegram.org |
| `API_HASH` | ✅ | Telegram API Hash from my.telegram.org |
| `OWNER_ID` | ✅ | Your Telegram user ID (numeric) |
| `MONGO_URI` | ✅ | MongoDB connection string |
| `MAIN_CHANNEL` | ❌ | Channel ID for library posts (e.g., -1001234567890) |
| `LOG_CHANNEL` | ❌ | Channel ID for bot logs |
| `LOG_LEVEL` | ❌ | Logging level (default: INFO) |

### Getting Credentials

1. **BOT_TOKEN**: Create a bot via [@BotFather](https://t.me/BotFather)
2. **API_ID & API_HASH**: Get from [my.telegram.org](https://my.telegram.org) → API Development Tools
3. **OWNER_ID**: Get from [@userinfobot](https://t.me/userinfobot)
4. **MONGO_URI**: Use Railway's MongoDB or [MongoDB Atlas](https://mongodb.com/atlas) free tier
5. **MAIN_CHANNEL**: Create a channel, add bot as admin, get ID via [@userinfobot](https://t.me/userinfobot)

## EC2 / VPS Docker Deployment (Recommended)

SSH into your server and run:

```bash
# Clone the repo
git clone https://github.com/jrodr254/animedekho-bot.git
cd animedekho-bot

# Run the setup script (does everything)
bash setup.sh
```

The setup script will:
1. **Install Docker** — removes old broken packages, adds official Docker repo, installs Docker Engine + Compose
2. **Ask for env variables** — API_ID, API_HASH, BOT_TOKEN, OWNER_ID, MAIN_CHANNEL, LOG_CHANNEL → saves to `.env`
3. **Build Docker image** — Python 3.11 + ffmpeg + N_m3u8DL-RE + yt-dlp + bot dependencies
4. **Start containers** — bot + MongoDB in detached mode

### Managing the bot

```bash
docker compose logs -f          # Live logs
docker compose restart bot      # Restart bot
docker compose down             # Stop everything
docker compose up -d --build    # Rebuild & restart
```

### What's included in the Docker image
- Python 3.11
- ffmpeg (for m3u8 stream conversion)
- N_m3u8DL-RE (advanced m3u8 downloader)
- yt-dlp (streaming server download tool)
- MongoDB 7 (separate container, data persisted in volume)

## Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Install ffmpeg (required for m3u8 downloads)
# Ubuntu/Debian:
sudo apt install ffmpeg
# macOS:
brew install ffmpeg

# Set environment variables
export BOT_TOKEN="your_bot_token"
export API_ID="12345"
export API_HASH="your_api_hash"
export OWNER_ID="your_telegram_id"
export MONGO_URI="mongodb://localhost:27017"

# Run
python main.py
```

## Bot Commands

### User Commands
- Just type any anime name to search

### Owner Commands
- `/adduser <id>` — Approve a user
- `/removeuser <id>` — Remove a user
- `/users` — List approved users
- `/setlogchannel <id>` — Set log channel
- `/setmainchannel <id>` — Set main channel
- `/setchannellink <url>` — Set force-sub invite link

## License

MIT
