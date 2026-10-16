"""Installed launcher; help and path selection do not initialize a model."""

import argparse


def main() -> int:
    """Start the existing loopback app with explicit writable locations."""
    parser = argparse.ArgumentParser(description="Run the local retrieval workbench")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--workspace", help="Base for relative paths and local video files"
    )
    parser.add_argument(
        "--data-dir", help="Library, indexes, runtime records, and caches"
    )
    parser.add_argument("--env-dir", help="Directory containing .env and .env.local")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    # Load dotenv and apply CLI overrides once, before imports freeze defaults.
    from local_preview.config import configure_startup

    overrides = {
        "YT_RAG_" + name.upper(): getattr(args, name)
        for name in ("workspace", "data_dir", "env_dir")
        if getattr(args, name)
    }
    config = configure_startup(overrides)
    from local_preview import local_api

    return local_api.main(["retrieval-lab", str(args.port)], config=config)


if __name__ == "__main__":
    raise SystemExit(main())
