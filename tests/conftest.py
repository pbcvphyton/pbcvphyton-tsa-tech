"""Configuração comum dos testes."""

from __future__ import annotations

from pathlib import Path

import pytest

from qijournal import config as config_module
from qijournal.edit import llm

# Os testes usam uma configuração fixa (jornal geral, com todas as editorias), e
# não a do jornal em produção (config/), que pode estar num nicho: assim mudar a
# linha editorial não quebra os testes do motor. A configuração real tem testes
# próprios (tests/test_config_nicho.py).
config_module.CONFIG_DIR = Path(__file__).parent / "fixtures" / "config"


@pytest.fixture(autouse=True)
def _no_llm_retry_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """A nova tentativa após erro passageiro da IA espera 15 s em produção; nos testes, não."""
    monkeypatch.setattr(llm, "RETRY_WAIT_SECONDS", 0.0)


@pytest.fixture(autouse=True)
def _no_real_ai_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Chaves de IA do ambiente de quem roda os testes nunca chamam as APIs de verdade."""
    for name in ("GEMINI_API_KEY", "MISTRAL_API_KEY", "AIMLAPI_KEY", "SENSENOVA_API_KEY", "MOONSHOT_API_KEY"):
        monkeypatch.delenv(name, raising=False)
