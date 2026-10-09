"""Configuração real do jornal (config/): o nicho do TSA Tech.

As áreas de atuação do TSA Advogados (tributário à frente) e, em segundo plano,
política e geopolítica. Os demais testes usam uma configuração fixa de jornal
geral (tests/fixtures/config); estes validam a linha editorial em produção: o
filtro de foco, as seções e seus tetos, as fontes, a orientação dada à IA e o
e-mail no formato newsletter.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from qijournal import pipeline
from qijournal.collect.feeds import compile_title_patterns, is_excluded_title
from qijournal.config import ROOT, background_sections, load_config
from qijournal.edit.cluster import focus_filter, in_focus
from qijournal.edit.llm import with_brief
from qijournal.models import Article
from qijournal.render.email import MAX_EMAIL_BYTES, render_email

REAL_BUNDLE = Path(__file__).parent / "fixtures" / "real" / "bundle-2026-09-29.json.gz"
AREAS = ["tributario", "previdenciario", "trabalhista", "recuperacao", "civel", "imobiliario"]


@pytest.fixture(scope="module")
def config():
    return load_config(root=ROOT, env={})


@pytest.fixture(scope="module")
def real_edition(config, tmp_path_factory):
    """Edição automática (sem IA) da coleta real de 29/09/2026 com a configuração real."""
    tmp = tmp_path_factory.mktemp("nicho")
    bundle_path = tmp / "bundle.json"
    bundle_path.write_bytes(gzip.open(REAL_BUNDLE).read())
    result = pipeline.run(config, out_dir=tmp / "out", bundle_path=bundle_path, use_llm=False, send_email=False, env={})
    return result.edition


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


def test_real_config_is_the_firm_niche(config):
    assert config.edition.focus_only is True
    ids = [s.id for s in config.sections]
    assert ids == [*AREAS, "politica", "mundo"]  # áreas do escritório na ordem do site, depois política e mundo
    caps = {s.id: s.max_stories for s in config.sections}
    assert caps["politica"] == 4 and caps["mundo"] == 3
    assert all(caps[area] is None for area in AREAS)
    assert background_sections(config.sections) == {"politica", "mundo"}
    assert config.brand.tagline == "Seu boletim jurídico diário"
    assert config.email.style == "newsletter"
    brief = config.llm.editorial_brief
    assert "Tributário" in brief and "no máximo 4" in brief and "no máximo 3" in brief
    # todo bloco da IA aponta para seções que existem, e toda seção está em algum bloco
    in_blocks = [s for group in config.llm.block_groups for s in group.sections]
    assert sorted(in_blocks) == sorted(ids)
    # dicas de seção das fontes só usam seções existentes; nenhum feed repetido
    for source in config.sources:
        assert set(source.topics) <= set(ids), (source.url, source.topics)
    assert any(source.niche for source in config.sources)
    assert len({source.url for source in config.sources}) == len(config.sources)


@pytest.mark.parametrize(
    "title",
    [
        # Tributário
        "STF veda ICMS sobre subvenção de energia elétrica",
        "Indecisão ameaça início do Imposto Seletivo",
        "Receita Federal abre consulta ao lote de restituição do IR",
        "Carf mantém autuação de R$ 2 bi contra mineradora",
        "PGFN lança edital de transação tributária para dívidas de até R$ 50 milhões",
        # Previdenciário
        "INSS adia prova de vida para aposentados",
        "STF retoma julgamento da revisão da vida toda",
        # Trabalhista
        "TST decide que motorista de aplicativo não tem vínculo de emprego",
        "Câmara instala comissão para discutir o fim da escala 6x1",
        # Recuperação de empresas
        "Ambipar obtém aval da Justiça dos EUA para centralizar recuperação judicial no Brasil",
        "Varejista pede falência após renegociação com credores fracassar",
        # Cível
        "Seguradora é condenada por não indenizar produtor rural",
        "Como a reforma do Código Civil pode encarecer a cobertura de seguros",
        # Imobiliário
        "Incorporadoras aceleram lançamentos em São Paulo",
        "Justiça autoriza despejo de inquilino inadimplente",
        # Política e geopolítica (segundo plano, mas no foco)
        "Lula sanciona lei aprovada pelo Congresso",
        "China e EUA acertam cortes de tarifas sobre US$ 60 bilhões em produtos",
    ],
)
def test_firm_areas_politics_and_geopolitics_are_in_focus(config, title):
    assert in_focus(article(title), config.sections)


@pytest.mark.parametrize(
    "title",
    [
        "Papa Leão XIV desafia a Europa a ceder posições pela paz",  # "leão" não é o Leão da Receita
        "Ibovespa fecha em alta puxado por bancos",
        "Bitcoin renova máxima histórica",
        "Flamengo vence o Palmeiras e assume a liderança",
        "Netflix anuncia nova temporada de série brasileira",
    ],
)
def test_other_news_is_out_of_focus(config, title):
    assert not in_focus(article(title), config.sections)


@pytest.mark.parametrize(
    "title",
    [
        "8 fundos imobiliários pagam dividendos nos últimos dias de setembro",
        "KNCR11 aprova nova emissão de cotas de até R$ 2,5 bilhões",
        "IFIX cai 0,12% e fecha pregão aos 3.737,17 pontos",
    ],
)
def test_real_estate_funds_are_excluded_at_collection(config, title):
    """FIIs são mercado, não o Imobiliário do escritório: saem já na coleta."""
    assert is_excluded_title(title, compile_title_patterns(config.edition.exclude_title_patterns))


def test_summary_alone_needs_two_mentions_of_the_same_area(config):
    once = article("Empresa anuncia fábrica nova", "A empresa terá benefício de ICMS no estado.")
    twice = article("Empresa anuncia fábrica nova", "Benefício de ICMS e redução do ISS atraíram a fábrica.")
    mixed = article("Fiscalização é reforçada nos postos", "O Procon fiscaliza; o governo estuda medida provisória.")
    assert not in_focus(once, config.sections)
    assert in_focus(twice, config.sections)
    assert not in_focus(mixed, config.sections)  # menções soltas a seções diferentes não bastam


def test_niche_feeds_enter_whole_and_other_feeds_are_filtered(config):
    # um veículo com tag especializada (niche) e capa geral (filtrada), como o InfoMoney
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


def test_real_day_edition_puts_the_firm_areas_first(config, real_edition):
    """Coleta real de 29/09/2026 (993 notícias): a edição sai, a manchete e as
    chamadas são das áreas do escritório e política/geopolítica respeitam o teto."""
    stories = {s.id: s for s in real_edition.stories}
    background = background_sections(config.sections)
    assert stories[real_edition.lead].section in AREAS
    assert all(stories[i].section not in background for i in real_edition.secondary)
    per_section = {s.id: len(s.story_ids) for s in real_edition.sections}
    assert per_section.get("politica", 0) <= 4 and per_section.get("mundo", 0) <= 3
    assert sum(per_section.get(area, 0) for area in AREAS) >= len(real_edition.stories) // 2
    assert [s.id for s in real_edition.sections][0] == "tributario"
    titles = " | ".join(s.headline for s in real_edition.stories)
    for intruder in ("Papa Leão", "pagam dividendos", "IFIX", "KNCR11"):
        assert intruder not in titles


def test_newsletter_email_is_minimal(config, real_edition):
    subject, html, plain = render_email(real_edition, config)
    assert len(html.encode("utf-8")) <= MAX_EMAIL_BYTES
    assert "<h1" in html and "Ler a matéria &rarr;" in html
    assert "Edição completa" in html and "Edições anteriores" in html
    assert "Dólar" not in html and "cv-a" not in html  # sem ticker nem barra de cobertura
    assert "assets/tsa-tech-logo-email.png" in html
    assert "MERCADOS" not in plain and "CLIMA" not in plain and "Cobertura:" not in plain
    assert plain.startswith("TSA TECH\n")
