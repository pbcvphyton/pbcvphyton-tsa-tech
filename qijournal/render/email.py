"""E-mail diário: assunto, HTML (tabelas + CSS inline) e versão em texto puro.

O HTML segue as restrições de clientes de e-mail (Gmail, Outlook, Apple Mail):
sem JavaScript, sem SVG, sem web fonts obrigatórias, largura de 640px e
tamanho total bem abaixo do corte de ~102 KB do Gmail. A newsletter
(``email.style: newsletter``) traz a manchete com foto e, abaixo, as principais
matérias num mosaico: lado a lado, na ordem do índice de relevância (metade a
importância dada pelo editor, metade a repercussão no Brasil e no exterior), com
a largura do bloco e o tamanho do título proporcionais ao índice.
"""

from __future__ import annotations

import logging
import math
import re
import textwrap
from dataclasses import dataclass
from datetime import date
from urllib.parse import quote

from .. import text
from ..config import Config
from ..models import Edition
from . import filters
from .web import EditionView, EmailLogo, StoryView, _email_logo, build_view

log = logging.getLogger(__name__)

DEFAULT_SUBJECT = "{brand} · {date}: {lead}"
SUBJECT_LEAD_MAX = 70
PREHEADER_MAX = 150
TEXT_WIDTH = 72
# A rotina do Claude copia o HTML inteiro no parâmetro htmlBody do Gmail: o
# e-mail precisa ser enxuto. Acima disso, matérias saem do e-mail (a edição
# completa tem todas) até caber.
MAX_EMAIL_BYTES = 40 * 1024
EMAIL_STYLES = ("jornal", "newsletter")  # email.style no site.yaml
_BETWEEN_TAGS = re.compile(r">\s*\n\s*<")  # só quebras de linha do template (nunca o espaço entre palavras)
# Índice de relevância (0-100) do mosaico da newsletter: metade a importância dada
# pelo editor (1-5), metade a repercussão (veículos que deram a notícia; um
# veículo estrangeiro vale INTERNATIONAL_WEIGHT, por mostrar alcance fora do
# Brasil), em escala logarítmica até REPERCUSSION_CAP pontos.
INTERNATIONAL_WEIGHT = 3
REPERCUSSION_CAP = 12
# Mosaico: largura útil da caixa (600 - bordas - margens internas), blocos por
# linha (alternando) e tamanho do título pelo índice.
MOSAIC_WIDTH = 542
MOSAIC_ROWS = (2, 3)
TILE_FONT_MIN, TILE_FONT_MAX = 15, 22  # linhas de 3 blocos (colunas estreitas): 1 px a menos
# Imagens de compartilhamento padrão (a mesma em toda matéria do veículo, como o
# logo do g1 e do Valor): no e-mail, a matéria aparece sem foto.
_GENERIC_PHOTO = re.compile(r"preview-share|fb_marca", re.I)


@dataclass
class EmailSection:
    title: str
    color: str
    stories: list[StoryView]


def story_url(page_url: str, story_id: str) -> str:
    """Link para a matéria na página da edição (o modal abre pela âncora ``#s-<id>``).

    ``render_email`` usa a cópia arquivada (``edicoes/AAAA-MM-DD.html``): a capa
    muda no dia seguinte, e o link de um e-mail antigo continuaria abrindo a
    matéria certa.
    """
    return f"{page_url}#s-{quote(story_id, safe='-_.~')}"


def _subject(edition: Edition, config: Config) -> str:
    try:
        short_date = text.pt_short_date(date.fromisoformat(edition.date))
    except ValueError:
        short_date = edition.date
    lead = ""
    try:
        lead = text.truncate(" ".join(edition.story(edition.lead).headline.split()), SUBJECT_LEAD_MAX)
    except KeyError:
        pass
    values = {
        "brand": config.brand.name,
        "date_label": edition.date_label,
        "date": short_date,
        "lead": lead or edition.date_label,
    }
    try:
        subject = config.email.subject_template.format(**values)
    except (KeyError, IndexError, ValueError) as exc:
        log.warning("subject_template inválido (%s); usando o padrão", exc)
        subject = DEFAULT_SUBJECT.format(**values)
    # Assunto é cabeçalho de e-mail: nunca pode conter quebras de linha.
    return " ".join(subject.split())


def photo(story: StoryView) -> str | None:
    """Foto da matéria para o e-mail (``None`` sem foto ou com imagem genérica do veículo)."""
    return story.image if story.image and not _GENERIC_PHOTO.search(story.image) else None


def _top_stories(view: EditionView, limit: int) -> list[StoryView]:
    """Newsletter: as ``limit`` matérias mais importantes além da manchete, na
    ordem de sempre (chamadas, destaques, demais), sem agrupar por seção."""
    lead_id = view.lead.id if view.lead else None
    seen: set[str] = set()
    top: list[StoryView] = []
    for story in [*view.secondary, *view.highlights, *view.stories]:
        if len(top) >= max(limit, 0):
            break
        if story.id != lead_id and story.id not in seen:
            seen.add(story.id)
            top.append(story)
    return top


@dataclass
class Relevance:
    """Relevância de uma matéria no cenário brasileiro e internacional."""

    score: int  # 0-100
    outlets: int  # veículos brasileiros que deram a notícia
    international: int  # veículos estrangeiros

    @property
    def label(self) -> str:
        """"7 veículos · 2 no exterior" (o que sustenta o tamanho do bloco)."""
        total = self.outlets + self.international
        text = f"{total} veículo{'s' if total != 1 else ''}"
        return text + (f" · {self.international} no exterior" if self.international else "")


@dataclass
class Tile:
    story: StoryView
    rank: int  # posição no dia (a manchete é a 1)
    width: int  # largura do bloco no desktop (px)
    font: int  # tamanho do título (px)
    relevance: Relevance


def _relevance(edition: Edition, stories: list[StoryView], config: Config) -> dict[str, Relevance]:
    """Índice de relevância de cada matéria: importância do editor + repercussão."""
    lang = {source.name: source.lang for source in config.sources}
    result = {}
    for story in stories:
        try:
            importance = edition.story(story.id).importance or 3
        except KeyError:
            importance = 3
        names = {source.name for source in story.sources}
        international = sum(1 for name in names if lang.get(name, "pt") != "pt")
        outlets = len(names) - international
        points = outlets + INTERNATIONAL_WEIGHT * international
        repercussion = min(1.0, math.log2(1 + points) / math.log2(1 + REPERCUSSION_CAP))
        score = 100 * (0.5 * max(1, min(5, importance)) / 5 + 0.5 * repercussion)
        result[story.id] = Relevance(score=round(score), outlets=outlets, international=international)
    return result


def _by_relevance(stories: list[StoryView], rel: dict[str, Relevance]) -> list[StoryView]:
    """Mais relevantes primeiro; no empate, a ordem do editor."""
    return sorted(stories, key=lambda s: -rel[s.id].score)


def _mosaic(stories: list[StoryView], rel: dict[str, Relevance], first_rank: int) -> list[list[Tile]]:
    """Linhas do mosaico (2 e 3 blocos, alternando), já na ordem de relevância.

    Na linha, a largura de cada bloco é proporcional ao índice (com um mínimo para
    o título caber) e soma :data:`MOSAIC_WIDTH`; o título vai de
    :data:`TILE_FONT_MIN` a :data:`TILE_FONT_MAX` px conforme o índice.
    """
    rows: list[list[Tile]] = []
    rank, i, k = first_rank, 0, 0
    while i < len(stories):
        chunk = stories[i : i + MOSAIC_ROWS[k % len(MOSAIC_ROWS)]]
        weights = [max(rel[s.id].score, 1) for s in chunk]
        floor = 0.8 / len(chunk)  # nenhum bloco abaixo de 80% da parte igual
        shares = [max(floor, w / sum(weights)) for w in weights]
        shares = [x / sum(shares) for x in shares]
        widths = [int(MOSAIC_WIDTH * x) for x in shares]
        widths[0] += MOSAIC_WIDTH - sum(widths)
        row = []
        for story, width in zip(chunk, widths):
            score = rel[story.id].score
            font = TILE_FONT_MIN + round((TILE_FONT_MAX - TILE_FONT_MIN) * max(0, score - 40) / 60)
            font = min(TILE_FONT_MAX, font) - (1 if len(chunk) >= 3 else 0)
            row.append(Tile(story, rank, width, font, rel[story.id]))
            rank += 1
        rows.append(row)
        i += len(chunk)
        k += 1
    return rows


def _newsletter_logo(view: EditionView, config: Config) -> EmailLogo | None:
    """Logo do topo da newsletter: ``email.logo`` ou, sem ele, o ``email_logo`` da marca."""
    return _email_logo(config.email.logo, view.base_url) or view.brand.email_logo


def _select_sections(view: EditionView, limit: int) -> list[EmailSection]:
    """Até ``limit`` matérias (além da manchete), agrupadas por seção na ordem da edição.

    1ª passada: a melhor matéria (fora a manchete) de cada seção não vazia, para
    nenhuma seção sumir do e-mail; 2ª passada: completa pela prioridade de
    sempre (chamadas, destaques, demais).
    """
    lead_id = view.lead.id if view.lead else None
    limit = max(limit, 0)
    priority = [*view.secondary, *view.highlights, *view.stories]
    rank: dict[str, int] = {}
    for story in priority:
        rank.setdefault(story.id, len(rank))
    chosen: list[str] = []
    for section in view.sections:
        if len(chosen) >= limit:
            break
        candidates = [s for s in section.stories if s.id != lead_id and s.id not in chosen]
        if candidates:
            chosen.append(min(candidates, key=lambda s: rank.get(s.id, len(rank))).id)
    for story in priority:
        if len(chosen) >= limit:
            break
        if story.id != lead_id and story.id not in chosen:
            chosen.append(story.id)
    selected = set(chosen)
    groups: list[EmailSection] = []
    for section in view.sections:
        stories = [s for s in section.stories if s.id in selected]
        if stories:
            groups.append(EmailSection(title=section.title, color=section.color, stories=stories))
    return groups


def _preheader(view: EditionView) -> str:
    source = filters.plain(view.editorial) or (view.lead.dek if view.lead else "") or view.brand.tagline
    return text.truncate(source, PREHEADER_MAX)


# ── texto puro ───────────────────────────────────────────────────────────────


def _wrap(value: str, indent: str = "", first: str | None = None) -> list[str]:
    return textwrap.wrap(
        value,
        width=TEXT_WIDTH,
        initial_indent=indent if first is None else first,
        subsequent_indent=indent,
        break_long_words=False,
        break_on_hyphens=False,
    ) or [first or indent]


def _heading(title: str) -> list[str]:
    title = title.upper()
    return ["", title, "-" * len(title)]


def _coverage_lines(story: StoryView, indent: str = "") -> list[str]:
    """"Cobertura: Pende para: … · 3 de 5 veículos. <conclusão>" (vazio sem cobertura comparada)."""
    if not story.coverage:
        return []
    conclusion = f" {story.coverage.conclusion}" if story.coverage.conclusion else ""
    return _wrap(f"Cobertura: {story.coverage.lean_label}.{conclusion}", indent)


def _story_lines(story: StoryView, base_url: str, bullet: str, *, minimal: bool = False) -> list[str]:
    indent = " " * len(bullet)
    lines = _wrap(story.headline, indent, first=bullet)
    if story.dek:
        lines += _wrap(story.dek, indent)
    if story.sources and not minimal:
        lines += _wrap(f"Fontes: {', '.join(s.name for s in story.sources)}", indent)
    if not minimal:
        lines += _coverage_lines(story, indent)
    lines.append(f"{indent}{story_url(base_url, story.id)}")
    return lines


def _quote_text(view: EditionView) -> str:
    parts = []
    for q in view.quotes:
        parts.append(f"{q.label} {q.display}" + (f" ({q.change})" if q.change else ""))
    return " · ".join(parts)


def _weather_text(view: EditionView) -> list[str]:
    lines = []
    for w in view.weather:
        now = f", {w.now_desc.lower()}" if w.now_desc else ""
        tomorrow = f"{w.tomorrow_desc.lower()}, " if w.tomorrow_desc else ""
        lines += _wrap(
            f"{w.city}: {w.now_temp}{now} (mín. {w.today_min} / máx. {w.today_max}) · "
            f"amanhã: {tomorrow}{w.tomorrow_min} a {w.tomorrow_max}"
        )
    return lines


def _render_text(
    view: EditionView,
    top: list[StoryView],
    sections: list[EmailSection],
    footer: dict[str, str],
    *,
    minimal: bool = False,
) -> str:
    """Versão em texto puro. ``minimal`` (newsletter): só a manchete e as
    principais matérias (``top``), sem cotações, clima, editorial, "Em 1
    minuto", racional, fontes e cobertura, como o HTML da newsletter."""
    base = view.base_url
    page = view.edition_url  # links das matérias: cópia arquivada (estável)
    dateline = view.date_label + (f" · {view.time_label} {view.tz_label}" if view.time_label else "")
    lines = [view.brand.name.upper(), dateline]
    if view.quotes and not minimal:
        lines += _heading("Mercados") + _wrap(_quote_text(view))
    if view.weather and not minimal:
        lines += _heading("Clima") + _weather_text(view)
    if view.editorial and not minimal:
        lines += _heading("Editorial") + _wrap(filters.plain(view.editorial))
    if view.briefing and not minimal:
        lines += _heading("Em 1 minuto")
        for item in view.briefing:
            lines += _wrap(filters.plain(item), "  ", first="• ")
    if view.rationale and not minimal:
        lines += _heading("Como foi compilada") + _wrap(view.rationale.summary)
        lines.append(f"Racional: {page}#racional")
        if view.index_url:
            lines.append(f"Todas as {filters.num(view.index_total)} notícias do dia: {view.index_url}")
    if view.lead:
        lead = view.lead
        lines += _heading("Manchete") + _wrap(lead.headline)
        if lead.dek:
            lines += _wrap(lead.dek)
        if lead.sources and not minimal:
            lines += _wrap(f"Fontes: {', '.join(s.name for s in lead.sources)}")
        if not minimal:
            lines += _coverage_lines(lead)
        lines.append(f"Ler na edição: {story_url(page, lead.id)}")
    if top:
        lines += _heading("Principais notícias")
        for story in top:
            lines += _story_lines(story, page, "• ", minimal=minimal) + [""]
        lines.pop()
    for section in sections:
        lines += _heading(section.title)
        for story in section.stories:
            lines += _story_lines(story, page, "• ", minimal=minimal) + [""]
        lines.pop()
    lines += [
        "",
        "=" * TEXT_WIDTH,
        f"Abrir edição completa: {base}",
    ]
    if minimal and view.index_url:
        lines.append(f"Todas as {filters.num(view.index_total)} notícias do dia: {view.index_url}")
    lines.append(f"Edições anteriores: {footer['archive_url']}")
    if view.repo_url and not minimal:
        lines.append(f"Código-fonte: {view.repo_url}")
    lines += _wrap(footer["generated"])
    return "\n".join(lines).strip() + "\n"


# ── API ──────────────────────────────────────────────────────────────────────


def _minify(html: str) -> str:
    """Tira a indentação e as quebras de linha entre tags (o visual não muda)."""
    return _BETWEEN_TAGS.sub("><", html).strip() + "\n"


def render_email(edition: Edition, config: Config) -> tuple[str, str, str]:
    """Gera ``(assunto, html, texto)`` do e-mail da edição.

    O HTML respeita :data:`MAX_EMAIL_BYTES`: se passar, as últimas matérias saem
    (uma a uma) até caber; a versão em texto traz as mesmas matérias.
    """
    view = build_view(edition, config)
    subject = _subject(edition, config)
    time_part = f" em {view.time_label} ({view.tz_label})" if view.time_label else ""
    footer = {
        "archive_url": f"{view.base_url}edicoes/",
        "generated": f"Gerado automaticamente{time_part} · {view.mode_label}",
    }
    newsletter = config.email.style == "newsletter"
    if config.email.style not in EMAIL_STYLES:
        log.warning("email.style %r desconhecido; usando 'jornal'", config.email.style)
    template = filters.environment().get_template("email_newsletter.html.j2" if newsletter else "email.html.j2")
    limit = max(config.email.max_stories, 0)
    while True:
        # Newsletter: as principais, em mosaico pela relevância; jornal: por seção.
        top = _top_stories(view, limit) if newsletter else []
        rel = _relevance(edition, top, config)
        top = _by_relevance(top, rel)
        mosaic = _mosaic(top, rel, first_rank=2 if view.lead else 1)
        sections = [] if newsletter else _select_sections(view, limit)
        html = _minify(
            template.render(
                v=view,
                brand=view.brand,
                subject=subject,
                preheader=_preheader(view),
                logo=_newsletter_logo(view, config),
                top=top,
                mosaic=mosaic,
                mosaic_width=MOSAIC_WIDTH,
                sections=sections,
                story_url=lambda story: story_url(view.edition_url, story.id),
                photo=photo,
                teaser=text.truncate,
                footer=footer,
            )
        )
        size = len(html.encode("utf-8"))
        shown = len(top) + sum(len(s.stories) for s in sections)
        if size <= MAX_EMAIL_BYTES or shown == 0:
            break
        limit = shown - 1
    if size > MAX_EMAIL_BYTES:
        log.warning(
            "HTML do e-mail com %.0f KB mesmo sem matérias além da manchete (limite: %d KB)",
            size / 1024,
            MAX_EMAIL_BYTES // 1024,
        )
    elif limit < config.email.max_stories:
        log.info("E-mail reduzido a %d matérias para caber em %d KB", shown, MAX_EMAIL_BYTES // 1024)
    plain_text = _render_text(view, top, sections, footer, minimal=newsletter)
    log.info(
        "E-mail renderizado: %d matérias em %d seções, %.0f KB",
        shown + (1 if view.lead else 0),
        len(sections),
        size / 1024,
    )
    return subject, html, plain_text
