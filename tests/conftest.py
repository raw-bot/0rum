"""Shared pytest configuration and fixtures.

Sets required environment variables before any src.* imports to prevent
pydantic_settings from failing on missing fields during collection.
"""

import os

# Remove any stale OANDA_ vars that pydantic-settings would reject as extra fields
for _oanda_key in ("OANDA_API_KEY", "OANDA_ACCOUNT_ID", "OANDA_API_URL"):
    os.environ.pop(_oanda_key, None)

# Set required env vars before any src module is imported — these override
# whatever is in .env (or substitute for a missing .env in the worktree).
# No broker credentials required — market data comes from Binance public API
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("TELEGRAM_CHAT_ID", "test-chat")
