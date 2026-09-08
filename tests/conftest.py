import pytest
from fastapi.testclient import TestClient

from app.core.simulation_engine import engine
from app.main import app


@pytest.fixture()
def client():
    engine.reset()
    for _ in range(5):
        engine.advance()

    with TestClient(app) as test_client:
        yield test_client
