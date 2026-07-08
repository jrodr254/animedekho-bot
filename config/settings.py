"""Centralized configuration."""

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SiteConfig:
    base_url: str = "https://animedekho.app"
    ajax_url: str = "https://animedekho.app/wp-admin/admin-ajax.php"
    series_path: str = "/series-hindi"
    movies_path: str = "/movies-hindi"
    episode_path: str = "/epi"
    embed_pattern: str = "https://animedekho.app/?trdekho={server}&trid={post_id}&trtype=2"
    category_api: str = "https://animedekho.app/wp-json/wp/v2/categories"
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    )
    bypass_cookie: dict = field(default_factory=lambda: {"toronites_server": "vidstream"})
    request_timeout: int = 15
    # Known trdekho server IDs
    server_ids: dict = field(default_factory=lambda: {
        0: "VidStream",
        1: "HydraX",
        2: "SRuby",
        3: "MirrorBot",
        4: "Server 5",
        5: "VidCloud",
        6: "Strmup",
        7: "Omega",
        8: "Vidmoly",
    })
    # Server priority order: VidStream first, HydraX fallback, Vidmoly last resort
    preferred_servers: list = field(default_factory=lambda: ["VidStream", "HydraX", "Vidmoly"])
    # Default quality buttons always shown to user
    default_qualities: list = field(default_factory=lambda: ["480p", "720p", "1080p"])


@dataclass(frozen=True)
class CacheConfig:
    default_ttl: int = 300
    search_ttl: int = 120
    categories_ttl: int = 3600
    listing_ttl: int = 180
    max_entries: int = 500


@dataclass(frozen=True)
class BotConfig:
    token: str = field(default_factory=lambda: os.environ.get("BOT_TOKEN", ""))
    api_id: int = field(default_factory=lambda: int(os.environ.get("API_ID", "0")))
    api_hash: str = field(default_factory=lambda: os.environ.get("API_HASH", ""))
    owner_id: int = field(default_factory=lambda: int(os.environ.get("OWNER_ID", "0")))
    main_channel: int = field(default_factory=lambda: int(os.environ.get("MAIN_CHANNEL", "0")))
    log_channel: int = field(default_factory=lambda: int(os.environ.get("LOG_CHANNEL", "0")))
    mongo_uri: str = field(default_factory=lambda: os.environ.get("MONGO_URI", "mongodb://localhost:27017"))
    items_per_page: int = 10
    max_search_results: int = 15
    max_genres: int = 20
    episodes_per_row: int = 5
    seasons_per_row: int = 4
    slug_max_len: int = 55
    log_level: str = field(default_factory=lambda: os.environ.get("LOG_LEVEL", "INFO"))


@dataclass(frozen=True)
class Settings:
    site: SiteConfig = field(default_factory=SiteConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    bot: BotConfig = field(default_factory=BotConfig)


settings = Settings()
