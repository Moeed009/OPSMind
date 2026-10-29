from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_migrations_have_single_head():
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    assert len(script.get_heads()) <= 1