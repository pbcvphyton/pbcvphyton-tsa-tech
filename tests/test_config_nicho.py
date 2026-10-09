"""Configuração real do jornal (config/): o nicho tributário do TSA Tech.

Os demais testes usam uma configuração fixa de jornal geral
(tests/fixtures/config); estes validam a linha editorial em produção: o filtro
de foco, as seções, as fontes especializadas e a orientação dada à IA.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from qijournal.config import ROOT, load_config
from qijournal.edit.cluster import classify, focus_filter, in_focus
from qijournal.edit.llm import with_brief
from qijournal.models import Article, Bundle

REAL_BUNDLE = Path(__file__).parent / "fixtures" / "real" / "bundle-2026-09-29.json.gz"


@pytest.fixture(scope="module")
def config():
    return load_config(root=ROOT, env={})


def article(title: str, summary: str = "", *, feed_url: str = "https://exemplo.com/feed/", source_id: str = "x") -> Article:
    return Article(
        id=title[:12],
        url=f"https://exemplo.com/{abs(hash(title))}",
        title=title,
        summary=summary,
        source_id=source_id,
        source_name="Exemplo",
        lang="pt",
        feed_url=feed_url,
    )


def test_real_config_is_the_tax_niche(config):
    assert config.edition.focus_only is True
    ids = [s.id for s in config.sections]
    assert ids == ["reforma", "federais", "estaduais", "contencioso", "previdencia", "fiscal", "internacional"]
    assert config.brand.tagline == "Seu boletim tributário diário"
    assert "tributári" in config.llm.editorial_brief
    # todo bloco da IA aponta para seções que existem, e toda seção está em algum bloco
    in_blocks = [s for group in config.llm.block_groups for s in group.sections]
    assert sorted(in_blocks) == sorted(ids)
    # dicas de seção das fontes só usam seções existentes
    for source in config.sources:
        assert set(source.topics) <= set(ids), (source.url, source.topics)
    assert any(source.niche for source in config.sources)
    assert len({source.url for source in config.sources}) == len(config.sources)  # sem feed repetido


@pytest.mark.parametrize(
    "title",
    [
        "STF veda ICMS sobre subvenção de energia elétrica",
        "Indecisão ameaça início do Imposto Seletivo",
        "Maioria das empresas no Simples deve optar por alíquotas cheias do IBS e CBS",
        "Receita Federal abre consulta ao lote de restituição do IR",
        "Carf mantém autuação de R$ 2 bi contra mineradora",
        "PGFN lança edital de transação tributária para dívidas de até R$ 50 milhões",
        "Câmara aprova desoneração da folha para 17 setores",
        "Governo estima perda de arrecadação com proibição das bets",
        "OECD global minimum tax deal faces new US pushback",
        "Redata aguarda regras sobre energia e importação após sanção",
    ],
)
def test_tax_news_is_in_focus(config, title):
    assert in_focus(article(title), config.sections)


@pytest.mark.parametrize(
    "title",
    [
        "Papa Leão XIV desafia a Europa a ceder posições pela paz",  # "leão" não é o Leão da Receita
        "Nova pesquisa Datafolha no DF mede se Celina Leão liquida eleição",
        "8 fundos imobiliários pagam dividendos nos últimos dias de setembro",  # dividendo não é tributo
        "Agenda de empresas: Multiplan anuncia JCP",
        "China e EUA acertam cortes de tarifas sobre US$ 60 bilhões em produtos",
        "Juros futuros sobem com pressão externa e pesquisas eleitorais",
        "Flamengo vence o Palmeiras e assume a liderança",
    ],
)
def test_other_news_is_out_of_focus(config, title):
    assert not in_focus(article(title), config.sections)


def test_summary_alone_needs_more_than_a_passing_mention(config):
    once = article("Empresa anuncia fábrica nova", "A empresa terá benefício de ICMS no estado.")
    twice = article("Empresa anuncia fábrica nova", "Benefício de ICMS e redução do ISS atraíram a fábrica.")
    assert not in_focus(once, config.sections)
    assert in_focus(twice, config.sections)


def test_niche_feeds_enter_whole_and_other_feeds_are_filtered(config):
    # um veículo com tag tributária (niche) e capa geral (filtrada), como o InfoMoney
    general_ids = {s.id for s in config.sources if not s.niche}
    niche = next(s for s in config.sources if s.niche and s.id in general_ids)
    same_outlet = next(s for s in config.sources if s.id == niche.id and not s.niche)
    kept = article("Entenda o calendário do novo sistema", feed_url=niche.url, source_id=niche.id)
    dropped = article("Entenda o calendário do novo sistema", feed_url=same_outlet.url, source_id=niche.id)
    tax = article("Receita prorroga prazo do IRPF", feed_url=same_outlet.url, source_id=niche.id)
    assert focus_filter([kept, dropped, tax], config) == [kept, tax]


def test_focus_filter_is_off_for_a_general_newspaper():
    general = load_config(env={})  # configuração fixa dos testes (jornal geral)
    assert general.edition.focus_only is False
    articles = [article("Flamengo vence o Palmeiras")]
    assert focus_filter(articles, general) == articles


def test_editorial_brief_closes_the_ai_instructions(config):
    system = with_brief("Instruções gerais.", config)
    assert system.startswith("Instruções gerais.")
    assert "Foco deste jornal (prevalece sobre os critérios acima)" in system
    assert system.rstrip().endswith(config.llm.editorial_brief.strip().splitlines()[-1].strip())
    general = load_config(env={})
    assert with_brief("Instruções gerais.", general) == "Instruções gerais."


def test_real_day_keeps_enough_tax_news(config):
    """Na coleta real de 29/09/2026 (993 notícias, só fontes gerais), o foco deixa
    notícias tributárias suficientes para a edição e nenhum falso positivo conhecido."""
    bundle = Bundle.from_dict(json.loads(gzip.open(REAL_BUNDLE).read()))
    kept = focus_filter(bundle.articles, config)
    assert config.edition.min_articles <= len(kept) < 60
    assert len({a.source_id for a in kept if a.lang == "pt"}) >= config.edition.min_pt_sources_ok
    titles = " | ".join(a.title for a in kept)
    for intruder in ("Papa Leão", "Celina Leão", "pagam dividendos", "anuncia JCP", "gastos do SUS"):
        assert intruder not in titles
    sections = {classify(f"{a.title} {a.summary}", a.topics, config.sections) for a in kept}
    assert {"reforma", "federais", "estaduais"} <= sections
