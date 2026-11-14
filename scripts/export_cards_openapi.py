"""Export the card router's generated contract without a running server."""

import json
from pathlib import Path

from fastapi import FastAPI

from card_servicing.cards import router


def main() -> None:
    application = FastAPI(
        title="Card Origination API", version="0.1.0",
        servers=[{"url": "http://127.0.0.1:8010", "description": "Local development"}],
    )
    application.include_router(router)
    destination = Path(__file__).resolve().parents[1] / "docs" / "cards.openapi.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(application.openapi(), indent=2) + "\n", encoding="utf-8")
    print(f"Exported {destination}")


if __name__ == "__main__":
    main()
