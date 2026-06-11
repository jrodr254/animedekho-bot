# AnimeDekho Telegram Bot 🎌

Telegram bot to search, browse, and stream anime from [AnimeDekho](https://animedekho.app/) — Hindi, Tamil & Telugu dubbed.

## Features

- 🔍 **Search** — Find any anime or movie via AJAX search API
- 📺 **Browse Series** — Paginated recent series listing
- 🎬 **Movies** — Browse dubbed movies
- 📂 **Genres** — Filter by category
- 📂 **Seasons & Episodes** — Full navigation with inline buttons
- ▶️ **Multi-Server** — Extracts real video player URLs from multiple CDN servers
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
│   └── resolver.py         # CDN player extractors
├── bot/
│   ├── keyboards.py        # Inline keyboard builders
│   ├── app.py              # App factory
│   └── handlers/           # Command & callback handlers
└── utils/
    ├── cache.py            # TTL cache
    ├── http.py             # HTTP client with caching
    └── helpers.py          # Utilities
```

## Setup

```bash
pip install -r requirements.txt
BOT_TOKEN="your_bot_token" python3 main.py
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `BOT_TOKEN` | ✅ | Telegram Bot API token |
| `LOG_LEVEL` | ❌ | Logging level (default: INFO) |

## License

MIT
