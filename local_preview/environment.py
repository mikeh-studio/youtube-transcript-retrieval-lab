"""Configuration shared by the local app and its offline tools."""


def load_app_environment(root):
    """Apply .env defaults, then .env.local overrides, from the app root."""
    try:
        from dotenv import load_dotenv
    except ImportError:  # The app can also be configured entirely by its environment.
        return
    load_dotenv(root / ".env")
    load_dotenv(root / ".env.local", override=True)
