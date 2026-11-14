import os
from pathlib import Path

from dotenv import dotenv_values


def settings():
    return {**dotenv_values(Path(__file__).resolve().parents[2] / '.env'), **os.environ}
