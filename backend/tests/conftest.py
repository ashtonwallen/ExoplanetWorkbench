import pytest
from exodiscovery import store


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA", tmp_path)
    store.init()
    return tmp_path
