"""Testes do e-mail diário (qijournal/render/email.py)."""

from __future__ import annotations

import copy
import re

import pytest

from qijournal import text
from qijournal.render.email import render_email, story_url
from tests.fixtures.render.helpers import LEAD_ID, MALICIOUS_ID, load_edition, parse, tsa_config, default_config

BASE = "https://pbcvphyton.github.io/pbcvphyton-tsa-tech/"
PAGE = BASE + "edicoes/2026-09-29.html"  # links das matérias: cópia arquivada da edição


@pytest.fixture(scope="module")
def config():
    return default_config()


@pytest.fixture(scope="module")
def edition():
    return load_edition()


@pytest.fixture(scope="module")
def rendered(edition, config):
    return render_email(edition, config)


def linked_story_ids(html: str) -> list[str]:
    """Ids das matérias com título linkado (a manchete aparece em <h1>, as demais em <h2>)."""
    return re.findall(r'<h[12][^>]*><a href="' + re.escape(PAGE) + r'#s-([^"]+)"', html)


# ── assunto ──────────────────────────────────────────────────────────────────


def expected_subject(edition, brand: str = "TSA Tech") -> str:
    lead = text.truncate(edition.story(edition.lead).headline, 70)
    return f"{brand} · 29/09/2026: {lead}"


def test_default_subject(rendered, edition):
    """O assunto traz o gancho do dia (a manchete, até 70 caracteres)."""
    subject, _, _ = rendered
    assert subject == expected_subject(edition)
    assert subject.startswith("TSA Tech · 29/09/2026: Arrecadação federal bate recorde")
    assert len(subject) <= len("TSA Tech · 29/09/2026: ") + 71


def test_subject_without_lead_and_with_line_breaks(edition):
    cfg = default_config()
    ed = copy.deepcopy(edition)
    ed.story(ed.lead).headline = "Linha um\nlinha dois"
    assert render_email(ed, cfg)[0] == "TSA Tech · 29/09/2026: Linha um linha dois"
    ed.lead = "nao-existe"
    assert render_email(ed, cfg)[0] == "TSA Tech · 29/09/2026: Terça-feira, 29 de setembro de 2026"


def test_custom_subject_template_with_short_date(edition):
    cfg = default_config()
    cfg.email.subject_template = "[{brand}] Edição de {date}"
    assert render_email(edition, cfg)[0] == "[TSA Tech] Edição de 29/09/2026"


@pytest.mark.parametrize("template", ["{brand} {desconhecido}", "{0}", "{brand"])
def test_invalid_subject_template_falls_back(edition, template):
    cfg = default_config()
    cfg.email.subject_template = template
    assert render_email(edition, cfg)[0] == expected_subject(edition)


def test_subject_has_no_line_breaks(edition):
    cfg = default_config()
    cfg.email.subject_template = "{brand}\r\nBcc: alguem@example.com\n{date_label}"
    subject = render_email(edition, cfg)[0]
    assert "\n" not in subject and "\r" not in subject


# ── HTML ─────────────────────────────────────────────────────────────────────


def test_html_is_email_safe(rendered):
    _, html, _ = rendered
    assert len(html.encode("utf-8")) < 90 * 1024
    lowered = html.lower()
    assert "<script" not in lowered and "<svg" not in lowered
    assert "fonts.googleapis" not in lowered and "@import" not in lowered
    assert "**" not in html
    assert 'width="640"' in html and "max-width:640px" in html
    structure = parse(html)
    assert structure.errors == [] and structure.stack == []


def test_html_uses_inline_styles_for_layout(rendered):
    _, html, _ = rendered
    # O <style> é só melhoria progressiva: todo bloco de conteúdo tem estilo inline.
    for tag, attrs in parse(html).tags:
        if tag in ("td", "p", "h1", "h2", "a") and attrs.get("class") != "ink":
            assert attrs.get("style"), (tag, attrs)


def test_preheader_is_hidden_and_plain(rendered):
    _, html, _ = rendered
    pre = re.search(r'<div style="display:none;[^"]*">(.*?)&#847;', html, re.S).group(1)
    assert pre.startswith("A cinco dias do primeiro turno, o mercado embute prêmio eleitoral")
    assert "*" not in pre and "<" not in pre


def test_header_ticker_weather_editorial_and_briefing(rendered):
    _, html, _ = rendered
    # Cabeçalho em bloco de cor (ardósia do site do TSA): logo em PNG (GitHub Pages) branco sobre o bloco,
    # igual nos dois modos (sem versão escura do logo).
    assert '<td align="center" bgcolor="#2F363C" class="px" style="padding:30px 20px 20px;background:#2F363C;' in html
    assert (
        '<img src="https://pbcvphyton.github.io/pbcvphyton-tsa-tech/assets/tsa-tech-logo-email.png"'
        ' width="160" height="87" alt="TSA Tech"' in html
    )
    assert "tsa-tech-logo-email-dark.png" not in html and 'class="lg-d"' not in html
    assert 'text-transform:uppercase;color:#D5D7D8;">Ao seu lado em todas as etapas do dia</p>' in html  # 80% sobre a ardósia
    assert 'Terça-feira, 29 de setembro de 2026 · 05:07 BRT</p>' in html  # data no bloco do cabeçalho
    assert "#687487" not in html  # cinza antigo, abaixo de AA sobre #f8f9fb
    assert (
        'Dólar <b style="color:#ffffff;font-weight:bold;">R$ 5,22</b> <span style="color:#91C3F5;">+0,19%</span>'
        in html
    )
    assert '<span style="color:#FF9C8A;">-0,27%</span>' in html
    # cidade e "Amanhã" em grupos separados: no celular (360 px) a linha quebra em vez de estourar
    # dois grupos inquebráveis por cidade: no celular a linha quebra depois do "|" (nunca começa com ele)
    assert 'São Paulo</b> 19°C ↓19° ↑33° |</span> <span style="white-space:nowrap">Amanhã' in html
    assert '<td class="px soft rule muted" align="center"' in html
    assert "<span style=\"color:#56627e;\"> · </span>" not in html  # ticker sem "·" pendurado nas quebras
    assert "<strong>prêmio eleitoral</strong>" in html
    assert "Em 1 minuto" in html and "<strong>R$ 212,4 bi</strong>" in html


def test_lead_with_image_and_link(rendered, edition):
    _, html, _ = rendered
    lead = edition.story(LEAD_ID)
    lead_url = f"{PAGE}#s-{LEAD_ID}"
    assert f'<img src="{lead.image}" width="600" alt="{lead.headline}"' in html
    assert f'<a href="{lead_url}" class="lnk" style="color:#2F6DB5">Ler na edição &rarr;</a>' in html
    assert linked_story_ids(html)[0] == LEAD_ID


def test_stories_are_limited_prioritized_and_grouped(rendered, edition, config):
    _, html, _ = rendered
    ids = linked_story_ids(html)[1:]
    assert len(ids) == config.email.max_stories == 14
    assert len(set(ids)) == len(ids) and LEAD_ID not in ids
    assert set(edition.secondary) <= set(ids) and set(edition.highlights) <= set(ids)
    # Agrupadas por seção, na ordem da edição.
    section_of = {s.id: s.section for s in edition.stories}
    order = [s.id for s in edition.sections]
    positions = [order.index(section_of[i]) for i in ids]
    assert positions == sorted(positions)
    titles = re.findall(r'<p class="sc sc-\d+" style="margin:0;padding:2px 0 2px 10px;[^"]*">([^<]+)</p>', html)
    assert len(titles) == len({section_of[i] for i in ids})


def test_max_stories_zero_keeps_only_the_lead(edition):
    cfg = default_config()
    cfg.email.max_stories = 0
    _, html, text = render_email(edition, cfg)
    assert linked_story_ids(html) == [LEAD_ID]
    assert text.count("#s-") == 1


def test_cta_and_footer(rendered):
    _, html, _ = rendered
    assert f'<a href="{BASE}" style="display:inline-block;' in html and "Abrir edição completa" in html
    assert f'<a href="{BASE}edicoes/"' in html
    assert '<a href="https://github.com/pbcvphyton/pbcvphyton-tsa-tech"' in html
    assert "Gerado automaticamente em 05:07 (BRT) · Edição gerada por IA (claude-opus-5-5)" in html
    assert "64 fontes consultadas" in html


def test_malicious_story_is_escaped_in_email(edition):
    cfg = default_config()
    cfg.email.max_stories = 50
    _, html, text = render_email(edition, cfg)
    assert MALICIOUS_ID in linked_story_ids(html)
    assert "<script>alert" not in html and "<img src=x" not in html
    assert "Teste &lt;script&gt;alert(1)&lt;/script&gt; &amp; &#34;aspas&#34; no título" in html
    assert "javascript:" not in html.lower() and "javascript:" not in text.lower()
    assert "https://example.com/materia?a=1&b=2" not in html  # só aparece escapado (&amp;)
    assert "Fonte &lt;i&gt;Maliciosa&lt;/i&gt;" in html
    assert len(html.encode("utf-8")) < 90 * 1024


def test_optional_blocks_are_omitted(edition, config):
    ed = copy.deepcopy(edition)
    ed.editorial, ed.briefing, ed.quotes, ed.weather = "", [], [], []
    ed.mode, ed.model = "heuristic", None
    _, html, text = render_email(ed, config)
    assert "font-style:italic" not in html  # bloco do editorial
    assert "Em 1 minuto" not in html and "EM 1 MINUTO" not in text
    assert "EDITORIAL" not in text and "MERCADOS\n" not in text and "CLIMA" not in text
    assert "Edição automática (sem IA)" in html and "Edição automática (sem IA)" in text
    # Sem editorial, o preheader usa a linha fina da manchete.
    assert re.search(r'<div style="display:none;[^"]*">Receita somou R\$ 212,4 bilhões', html)


def test_tsa_brand_email_uses_text_wordmark(edition):
    subject, html, _ = render_email(edition, tsa_config())
    assert subject.startswith("TSA Advogados · 29/09/2026: ")
    assert "<svg" not in html.lower()
    assert '<span style="color:#91C3F5;">TSA</span>' in html
    assert "background:#232A30;" in html
    assert "<img src=\"https://pbcvphyton.github.io/pbcvphyton-tsa-tech/assets/" not in html  # sem logo de e-mail
    assert 'class="px" style="padding:28px 20px 18px;border-bottom:3px solid #232A30;"' in html  # cabeçalho no papel


def test_email_logo_falls_back_to_text(edition):
    cfg = default_config()
    cfg.brand.email_logo = {"src": "javascript:alert(1)", "width": 212, "height": 44}
    _, html, _ = render_email(edition, cfg)
    assert "javascript:" not in html and "<img src=\"https://pbcvphyton" not in html
    # sem logo, o wordmark vai em on_masthead (branco) sobre o bloco do cabeçalho
    assert '<span style="color:#FFFFFF;">TSA</span><span style="color:#FFFFFF;font-weight:900;"> Tech</span>' in html


def test_solid_masthead_email_uses_blended_tones(edition):
    cfg = default_config()
    cfg.brand.colors["masthead"] = "#3322CC"
    cfg.brand.colors["on_masthead"] = "#F5F3ED"
    _, html, _ = render_email(edition, cfg)
    assert '<td align="center" bgcolor="#3322CC" class="px" style="padding:30px 20px 20px;background:#3322CC;' in html
    assert 'text-transform:uppercase;color:#CEC9E6;">Ao seu lado em todas as etapas do dia</p>' in html  # 80% sobre o azul


# ── texto puro ───────────────────────────────────────────────────────────────


def test_text_version_is_plain_and_complete(rendered, edition):
    _, html, text = rendered
    assert "**" not in text
    assert not re.search(r"</?(strong|a|p|b|span|div|br)\b", text)
    assert text.startswith("TSA TECH\nTerça-feira, 29 de setembro de 2026 · 05:07 BRT\n")
    for heading in ("MERCADOS", "CLIMA", "EDITORIAL", "EM 1 MINUTO", "MANCHETE", "BRASIL · ECONOMIA"):
        assert f"\n{heading}\n{'-' * len(heading)}\n" in text
    assert "prêmio eleitoral nos" in text
    assert "Dólar R$ 5,22 (+0,19%)" in text and "Selic 13,75%" in text
    assert f"Ler na edição: {PAGE}#s-{LEAD_ID}" in text
    # Mesmas matérias do HTML, com links por extenso.
    for story_id in linked_story_ids(html):
        assert f"{PAGE}#s-{story_id}" in text
    assert f"Abrir edição completa: {BASE}\n" in text
    assert f"Edições anteriores: {BASE}edicoes/\n" in text
    assert text.endswith("(claude-opus-5-5)\n")
    assert "Gerado automaticamente em 05:07 (BRT) · Edição gerada por IA (claude-opus-5-5)" in " ".join(text.split())


def test_text_lines_are_wrapped(rendered):
    _, _, text = rendered
    long_lines = [ln for ln in text.splitlines() if len(ln) > 72 and "https://" not in ln]
    assert long_lines == []


def test_story_url_encodes_unexpected_characters():
    assert story_url(BASE, "copom-mantem-selic") == f"{BASE}#s-copom-mantem-selic"
    assert story_url(BASE, 'a b"<c') == f"{BASE}#s-a%20b%22%3Cc"


# ── regressões da edição real de 29/09/2026 ─────────────────────────────────


def test_every_section_gets_at_least_one_story_in_the_email(edition):
    """Antes, Imobiliário e Tecnologia (além da manchete) ficavam de fora do e-mail."""
    cfg = default_config()
    cfg.email.max_stories = 8
    _, html, _ = render_email(edition, cfg)
    ids = linked_story_ids(html)[1:]
    section_of = {s.id: s.section for s in edition.stories}
    with_stories = {sec.id for sec in edition.sections if any(sid != LEAD_ID for sid in sec.story_ids)}
    assert {section_of[i] for i in ids} == with_stories
    assert len(ids) == 8


def test_email_is_slim_and_shows_at_most_three_sources(edition):
    from qijournal.models import SourceRef

    cfg = default_config()
    inflated = copy.deepcopy(edition)
    for story in inflated.stories:
        story.sources = [SourceRef(name=f"Veículo {i}", url=f"https://v{i}.example.com/{story.id}") for i in range(10)]
    _, html, _ = render_email(inflated, cfg)
    assert len(html.encode("utf-8")) <= 40 * 1024
    assert "Veículo 3" not in html and "· +7" in html
    # só a primeira fonte de cada matéria leva link
    assert html.count('href="https://v0.example.com/') >= 1 and 'href="https://v1.example.com/' not in html
    assert "\n  <" not in html  # sem indentação do template


def test_email_budget_drops_stories_until_it_fits(edition, monkeypatch):
    from qijournal.render import email as email_module

    cfg = default_config()
    monkeypatch.setattr(email_module, "MAX_EMAIL_BYTES", 20 * 1024)
    _, html, text = render_email(edition, cfg)
    assert len(html.encode("utf-8")) <= 20 * 1024
    shown = linked_story_ids(html)
    assert shown[0] == LEAD_ID and 1 <= len(shown) - 1 < cfg.email.max_stories
    assert text.count("#s-") == len(shown)  # texto puro com as mesmas matérias


def test_heuristic_email_skips_the_one_minute_block(edition, config):
    ed = copy.deepcopy(edition)
    ed.mode, ed.model = "heuristic", None
    _, html, _ = render_email(ed, config)
    assert "Em 1 minuto" not in html


def test_english_story_is_marked_in_the_email(edition, config):
    ed = copy.deepcopy(edition)
    ed.story(LEAD_ID).lang = "en"
    _, html, _ = render_email(ed, config)
    assert re.search(r'<h1 class="hl"[^>]*lang="en"', html)


def test_coverage_line_in_html_and_text(edition):
    from qijournal.models import Coverage, CoverageOutlet

    ed = copy.deepcopy(edition)
    ed.story(LEAD_ID).coverage = Coverage(
        topic="Arrecadação",
        conclusion="A maioria destacou o recorde.",
        outlets=[CoverageOutlet(name=n, stance=st) for n, st in (("Valor", "a"), ("Folha", "a"), ("g1", "b"))],
        side_a="Destaca o recorde",
        side_b="Destaca o risco fiscal",
    )
    _, html, text = render_email(ed, default_config())
    assert '<td width="67%" height="6" bgcolor="#2F6DB5"' in html and '<td width="33%" height="6" bgcolor="#C8631A"' in html
    assert "Pende para: Destaca o recorde · 2 de 3 veículos</b> · A maioria destacou o recorde." in html
    flat = " ".join(text.split())  # o texto puro quebra as linhas
    assert "Cobertura: Pende para: Destaca o recorde · 2 de 3 veículos. A maioria destacou o recorde." in flat
    assert parse(html).errors == []


def test_dark_mode_keeps_each_section_color(rendered):
    _, html, _ = rendered
    # cada seção com a própria cor clareada no escuro (não um azul único para todas)
    assert re.search(r"\.sc-1\{color:#[0-9A-F]{6}!important;border-left-color:#[0-9A-F]{6}!important;\}", html)
    assert '<p class="sc sc-1" style=' in html
    assert ".cv-a{background:#8DAFD6!important;}" in html  # primária clareada para o fundo escuro




# ── newsletter (email.style: newsletter) ─────────────────────────────────────

LOGO = {"src": "assets/tsa-tech-cabecalho-email.jpg", "width": 600, "height": 132}


def newsletter_config(**email):
    cfg = default_config()
    cfg.email.style = "newsletter"
    cfg.email.logo = dict(LOGO)
    for key, value in email.items():
        setattr(cfg.email, key, value)
    return cfg


@pytest.fixture(scope="module")
def newsletter(edition):
    return render_email(edition, newsletter_config())


def top_ids(html: str) -> list[str]:
    """Ids das principais matérias da newsletter (títulos em <h2>)."""
    return re.findall(r'<h2[^>]*><a href="' + re.escape(PAGE) + r'#s-([^"]+)"', html)


def expected_top(edition, limit: int) -> list[str]:
    """Chamadas, destaques e demais, sem repetir nem a manchete: as ``limit`` primeiras."""
    order = list(dict.fromkeys([*edition.secondary, *edition.highlights, *(s.id for s in edition.stories)]))
    return [i for i in order if i != LEAD_ID][:limit]


def test_newsletter_is_light_slim_and_email_safe(newsletter):
    _, html, _ = newsletter
    assert len(html.encode("utf-8")) <= 30 * 1024  # 15 matérias no exemplo; o limite da rotina é 40 KB
    lowered = html.lower()
    assert "<script" not in lowered and "<svg" not in lowered and "fonts.googleapis" not in lowered
    assert '<meta name="color-scheme" content="light only">' in html and "prefers-color-scheme" not in html
    assert '<body style="margin:0;padding:0;background:#EDF2F5;">' in html and "max-width:600px" in html
    assert "**" not in html and "&#39;" not in html
    structure = parse(html)
    assert structure.errors == [] and structure.stack == []


def test_newsletter_masthead_is_the_static_site_header(newsletter):
    _, html, _ = newsletter
    # o cabeçalho do site: o logo sobre o fundo de pontos e linhas numa imagem estática
    # que ocupa a faixa inteira (nada de GIF animado), sobre a ardósia com o fio marinho
    assert '<td align="center" bgcolor="#2F363C" style="padding:0 0 20px;background:#2F363C;border-bottom:3px solid #232A30;">' in html
    assert (
        f'<a href="{BASE}" style="text-decoration:none;"><img src="{BASE}assets/tsa-tech-cabecalho-email.jpg"'
        ' width="600" height="132" alt="TSA Tech"'
        ' style="display:block;width:100%;max-width:600px;height:auto;' in html
    )
    assert ".gif" not in html
    # como no site, o slogan e a data em texto na mesma faixa: se o cliente de e-mail
    # tirar a imagem (o conector do Gmail tira), a faixa continua com os dois
    head = html.split("<img src=", 2)[1].split("</td></tr>", 1)[0]
    assert 'text-transform:uppercase;color:#D5D7D8;">Ao seu lado em todas as etapas do dia</p>' in head
    assert "monospace;letter-spacing:1px;color:#C5C7C8;\">Terça-feira, 29 de setembro de 2026</p>" in head
    # foco nas notícias: sem cotações, clima, editorial nem "Em 1 minuto" (o editorial
    # fica só no preheader, o resumo que a caixa de entrada mostra ao lado do assunto)
    for absent in ("Dólar", "São Paulo", "Em 1 minuto", "italic", "<strong>prêmio eleitoral</strong>"):
        assert absent not in html, absent


def test_newsletter_survives_image_stripping(newsletter):
    """O conector do Gmail tira todas as imagens: sem elas, a faixa mantém o slogan e a
    data e um fio fino continua separando a manchete das demais."""
    _, html, _ = newsletter
    import re as _re

    bare = _re.sub(r"<a [^>]*><img [^>]*></a>", "", html)
    assert "<img" not in bare
    assert "Ao seu lado em todas as etapas do dia</p>" in bare and "Terça-feira, 29 de setembro de 2026</p>" in bare
    second = bare.split("<h1", 1)[1].split("<h2", 1)[0]
    assert "border-top:1px solid #E2E2E2;" in second


BOX = "border:1px solid #D2D7DE;border-top:3px solid #2F363C;"


def test_newsletter_stories_are_grouped_in_a_single_box(newsletter):
    _, html, _ = newsletter
    assert html.count(BOX) == 1
    inside = html.split(BOX, 1)[1].split("</table></td></tr>", 1)[0]
    # todas as notícias do dia na mesma caixa, que anuncia quantas são
    total = 1 + newsletter_config().email.max_stories
    assert f">Hoje em {total} notícias</td>" in inside
    assert inside.count("<h1") == 1 and inside.count("<h2") == html.count("<h2") == total - 1


def by_relevance(edition, ids: list[str]) -> list[str]:
    """Os ids na ordem do índice de relevância (no empate, a ordem do editor)."""
    from qijournal.render.email import _relevance
    from qijournal.render.web import build_view

    cfg = newsletter_config()
    view = build_view(edition, cfg)
    stories = [s for s in view.stories if s.id in ids]
    stories.sort(key=lambda s: ids.index(s.id))
    rel = _relevance(edition, stories, cfg)
    return [s.id for s in sorted(stories, key=lambda s: -rel[s.id].score)]


def test_newsletter_mosaic_follows_relevance(newsletter, edition):
    _, html, text = newsletter
    limit = newsletter_config().email.max_stories
    # as mesmas principais do editor, mas no mosaico na ordem da relevância
    assert top_ids(html) == by_relevance(edition, expected_top(edition, limit))
    # cada linha do mosaico (2 e 3 blocos, alternando) ocupa a linha inteira, em %: os
    # blocos cabem lado a lado em qualquer largura (arredondado para baixo, sem quebrar)
    rows = re.findall(r'<td style="padding:0;border-top:1px solid #E2E2E2;font-size:0;line-height:0;">(.*?)</td></tr>', html)
    widths = [[float(w) for w in re.findall(r'class="col" style="display:inline-block;width:([\d.]+)%;', row)] for row in rows]
    assert [len(w) for w in widths][:3] == [2, 3, 2] and all(99.0 <= sum(w) <= 99.9 + 1e-9 for w in widths)
    assert "max-width:542px;vertical-align" not in html  # nada de largura fixa nos blocos
    # título cada vez menor (ou igual) na ordem da relevância; posição e repercussão em cada bloco
    fonts = [int(px) for px in re.findall(r"<h2 style=\"margin:0;font:bold (\d+)px/", html)]
    assert all(15 - 1 <= f <= 22 for f in fonts)
    assert '<span style="color:#121212;">2</span>&nbsp;·&nbsp;' in html
    assert html.count('font:11px/15px Helvetica Neue,Helvetica,Arial,sans-serif;color:#6B6B6B;">') == limit
    assert re.search(r'color:#6B6B6B;">\d+ veículos?( · \d+ no exterior)?</p>', html)
    # só no celular (até 480 px) os blocos viram uma coluna
    assert "@media (max-width:480px){" in html and ".col{display:block!important;width:100%!important;}" in html
    # texto puro na mesma ordem
    positions = [text.index(f"#s-{i}") for i in top_ids(html)]
    assert positions == sorted(positions)


def test_relevance_index_and_mosaic_widths():
    from qijournal.render.email import Relevance, Tile, _mosaic

    assert Relevance(score=70, outlets=5, international=2).label == "7 veículos · 2 no exterior"
    assert Relevance(score=40, outlets=1, international=0).label == "1 veículo"
    stories = [type("S", (), {"id": f"s{i}"})() for i in range(5)]
    scores = {"s0": 90, "s1": 60, "s2": 55, "s3": 50, "s4": 45}
    rel = {k: Relevance(score=v, outlets=1, international=0) for k, v in scores.items()}
    rows = _mosaic(stories, rel, first_rank=2)
    assert [[t.story.id for t in row] for row in rows] == [["s0", "s1"], ["s2", "s3", "s4"]]
    assert [t.rank for row in rows for t in row] == [2, 3, 4, 5, 6]
    assert all(sum(t.width for t in row) == 542 for row in rows)
    assert all(99.0 <= sum(t.pct for t in row) <= 99.9 + 1e-9 for row in rows)
    first, second = rows[0]
    assert first.width > second.width and first.font > second.font  # o mais relevante é maior
    assert all(isinstance(t, Tile) and t.width >= int(542 * 0.8 / len(row)) for row in rows for t in row)


def test_relevance_counts_international_outlets(edition):
    from qijournal.models import SourceRef
    from qijournal.render.email import _relevance
    from qijournal.render.web import build_view

    cfg = newsletter_config()
    ed = copy.deepcopy(edition)
    story = ed.story(LEAD_ID)
    story.importance = 4
    story.sources = [SourceRef(name="Valor Econômico", url="https://valor.globo.com/a")]
    local = _relevance(ed, [build_view(ed, cfg).lead], cfg)[LEAD_ID]
    story.sources = [*story.sources, SourceRef(name="Financial Times", url="https://www.ft.com/a")]
    abroad = _relevance(ed, [build_view(ed, cfg).lead], cfg)[LEAD_ID]
    assert (local.outlets, local.international) == (1, 0)
    assert (abroad.outlets, abroad.international) == (1, 1) and abroad.score > local.score


def test_newsletter_cta_closes_the_box(newsletter):
    _, html, _ = newsletter
    inside = html.split(BOX, 1)[1]
    assert inside.index("Ler a edição completa &rarr;") < inside.index("</table></td></tr>")
    assert f'<a href="{BASE}" style="color:#121212;text-decoration:underline;">Ler a edição completa &rarr;</a>' in html


def test_newsletter_logo_falls_back_to_the_brand_email_logo(edition):
    # sem email.logo, o logo da marca (pequeno) fica centralizado na faixa, com o slogan em texto
    _, html, _ = render_email(edition, newsletter_config(logo=None))
    assert f'<img src="{BASE}assets/tsa-tech-logo-email.png" width="160" height="87" alt="TSA Tech"' in html
    assert 'style="padding:28px 0 20px;background:#2F363C;' in html
    assert 'text-transform:uppercase;color:#D5D7D8;">Ao seu lado em todas as etapas do dia</p>' in html
    _, html, _ = render_email(edition, newsletter_config(logo={"src": "javascript:alert(1)", "width": 160, "height": 87}))
    assert "javascript:" not in html and "tsa-tech-logo-email.png" in html


def test_newsletter_lead_has_photo_kicker_and_credit(newsletter, edition):
    _, html, _ = newsletter
    lead = edition.story(LEAD_ID)
    # a foto da manchete vem logo depois do texto dela e divide a caixa
    photo = f'<img class="ph" src="{lead.image}" width="542" alt="{lead.headline}"'
    assert photo in html and "height:220px;object-fit:cover;" in html
    assert re.search(r'<h1 class="r1"[^>]*><a href="' + re.escape(f"{PAGE}#s-{LEAD_ID}") + '"', html)
    assert html.index("<h1") < html.index(photo) < html.index("<h2")
    names = [src.name for src in lead.sources[:2]]
    assert " · ".join(names) in html
    # só a manchete tem foto, a dela mesma (além do logo)
    assert html.count("<img") == 2


def test_newsletter_top_stories_follow_importance(newsletter, edition):
    _, html, text = newsletter
    limit = newsletter_config().email.max_stories
    assert sorted(top_ids(html)) == sorted(expected_top(edition, limit))  # as principais do editor
    assert "<h3" not in html  # sem seções
    # texto puro: as mesmas matérias, sem os blocos que o HTML não tem
    assert text.startswith("TSA TECH" + chr(10))
    for heading in ("MANCHETE", "PRINCIPAIS NOTÍCIAS"):
        assert f"\n{heading}\n" in text
    for heading in ("MERCADOS", "CLIMA", "EDITORIAL", "EM 1 MINUTO"):
        assert f"\n{heading}\n" not in text
    assert all(f"{PAGE}#s-{i}" in text for i in [LEAD_ID, *top_ids(html)])


def test_newsletter_max_stories(edition):
    _, html, text = render_email(edition, newsletter_config(max_stories=3))
    assert top_ids(html) == by_relevance(edition, expected_top(edition, 3))
    assert text.count("#s-") == 4


def test_newsletter_budget_drops_stories_until_it_fits(edition, monkeypatch):
    from qijournal.render import email as email_module

    full = render_email(edition, newsletter_config())[1]
    monkeypatch.setattr(email_module, "MAX_EMAIL_BYTES", len(full.encode("utf-8")) - 1500)
    _, html, text = render_email(edition, newsletter_config())
    assert len(html.encode("utf-8")) <= email_module.MAX_EMAIL_BYTES
    shown = top_ids(html)
    assert 1 <= len(shown) < newsletter_config().email.max_stories
    assert shown == by_relevance(edition, expected_top(edition, len(shown)))
    assert text.count("#s-") == len(shown) + 1  # texto puro com as mesmas matérias (e a manchete)


def test_newsletter_footer(newsletter):
    _, html, _ = newsletter
    assert f'<a href="{BASE}edicoes/"' in html and "Edições anteriores</a></p>" in html
    assert "TSA Tech · Ao seu lado em todas as etapas do dia · 64 fontes · <a " in html
    assert "github.com" not in html
