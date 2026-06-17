import os
import sys
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
# テストでは実メールを一切送らない（ログ出力のみ）。送信キューのドレイナも smtp 時のみ起動するため
# これで「自動テストでの実メール送信」を構造的に禁止する。
os.environ["MAIL_BACKEND"] = "console"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.models import *  # noqa: F401,F403 – register all models

# テスト全体で共有する単一の SQLite エンジン（StaticPool で同一コネクション）
TEST_ENGINE = create_engine(
    "sqlite+pysqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSession = sessionmaker(bind=TEST_ENGINE, autocommit=False, autoflush=False)


def _override_get_db():
    Base.metadata.create_all(bind=TEST_ENGINE)
    db = TestSession()
    try:
        yield db
    finally:
        db.close()


# アプリ全体の DB 依存を共有エンジンに向ける
app.dependency_overrides[get_db] = _override_get_db


@pytest.fixture(autouse=True)
def reset_db():
    Base.metadata.drop_all(bind=TEST_ENGINE)
    Base.metadata.create_all(bind=TEST_ENGINE)
    yield
    Base.metadata.drop_all(bind=TEST_ENGINE)
