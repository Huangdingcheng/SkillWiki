"""Portable defaults isolate runtime state and explicitly bind optional config."""
from pathlib import Path

import pytest

from skillwiki.api.main import _default_skill_storage_dir
from skillwiki.layers.skill_runtime.skillnet_fabric_adapter import _paperqa_env_path


def test_default_store_is_independent_of_bundled_samples(tmp_path, monkeypatch):
    monkeypatch.delenv("SKILLWIKI_STORAGE_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    assert _default_skill_storage_dir() == tmp_path / "skillwiki-data"
    assert not _default_skill_storage_dir().exists()


def test_direct_api_honors_explicit_store(tmp_path, monkeypatch):
    monkeypatch.setenv("SKILLWIKI_STORAGE_DIR", str(tmp_path / "chosen"))
    assert _default_skill_storage_dir() == tmp_path / "chosen"


def test_remote_embeddings_require_explicit_config(monkeypatch):
    monkeypatch.delenv("SKILLWIKI_PAPERQA_ENV_FILE", raising=False)
    with pytest.raises(RuntimeError, match="SKILLWIKI_PAPERQA_ENV_FILE"):
        _paperqa_env_path()


def test_remote_embeddings_preserve_selected_path(tmp_path, monkeypatch):
    selected = tmp_path / "provider.env"
    monkeypatch.setenv("SKILLWIKI_PAPERQA_ENV_FILE", str(selected))
    assert _paperqa_env_path() == Path(selected)
