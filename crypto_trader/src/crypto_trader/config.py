"""Environment-backed configuration."""

from dataclasses import dataclass
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    exchange_id: str = "binance"
    database_path: Path = Path("data/market_data.sqlite3")
    default_timeframe: str = "1h"
    default_days: int = 30
    symbols: tuple[str, ...] = ("BTC/USDT", "ETH/USDT")

    @classmethod
    def from_env(cls) -> "Settings":
        symbols = tuple(
            item.strip() for item in os.getenv("SYMBOLS", "BTC/USDT,ETH/USDT").split(",")
            if item.strip()
        )
        days = int(os.getenv("DEFAULT_DAYS", "30"))
        if days < 1:
            raise ValueError("DEFAULT_DAYS must be >= 1")
        return cls(
            exchange_id=os.getenv("EXCHANGE_ID", "binance").strip().lower(),
            database_path=Path(os.getenv("DATABASE_PATH", "data/market_data.sqlite3")),
            default_timeframe=os.getenv("DEFAULT_TIMEFRAME", "1h").strip(),
            default_days=days,
            symbols=symbols,
        )
