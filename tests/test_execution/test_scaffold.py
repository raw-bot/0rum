"""Scaffold test ensuring tests/test_execution is a collectable pytest package."""


def test_execution_package_scaffold() -> None:
    """Keep pytest collect-only green until Phase 7 execution tests land."""
    assert True


def test_main_imports_without_telegram_dependency() -> None:
    """Application module imports without Telegram-specific dependencies."""
    import src.main as main

    assert main.app.title == "0rum"
