"""Export the card router's generated contract without a running server."""

import json
import argparse
from pathlib import Path

from fastapi import FastAPI

from card_servicing.cards import router


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    application = FastAPI(
        title="Card Origination API", version="0.1.0",
        servers=[{"url": "http://127.0.0.1:8010", "description": "Local development"}],
    )
    application.include_router(router)
    destination = Path(__file__).resolve().parents[1] / "docs" / "cards.openapi.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(application.openapi(), indent=2) + "\n"
    if args.check:
        if not destination.exists() or destination.read_text(encoding='utf-8') != content:
            parser.exit(1, 'Cards OpenAPI export is stale. Run python scripts/export_cards_openapi.py\n')
        print('Cards OpenAPI export is current.')
        return
    destination.write_text(content, encoding="utf-8")
    print(f"Exported {destination}")


if __name__ == "__main__":
    main()
