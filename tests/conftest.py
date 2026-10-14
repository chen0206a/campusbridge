import pytest

from campusbridge.db import Database
from campusbridge.security import Authentication, DEMO_PASSWORD
from campusbridge.settings import Settings
from campusbridge.store import Store


@pytest.fixture
def foundation(tmp_path):
    config = Settings(environment="test", database_path=tmp_path / "campus.sqlite3")
    database = Database(config.db_file)
    auth = Authentication(database, config)
    auth.seed()
    student = auth.authenticate(auth.login("student_lan", DEMO_PASSWORD)["access_token"])
    counselor = auth.authenticate(auth.login("counselor_yu", DEMO_PASSWORD)["access_token"])
    yield database, config, auth, Store(database), student, counselor
    database.close()
