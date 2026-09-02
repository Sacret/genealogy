"""Поиск фамилии в OCR-тексте."""

import re
from dataclasses import dataclass

from .normalize import normalize, normalize_marked, dehyphenate
from .match import prefix_distance, default_threshold, score

_TOKEN_RE = re.compile(r"[А-Яа-яЁёѢѣІіЇїѲѳѴѵЅѕѦѧѪѫA-Za-z]+")

# Падежные окончания, которые срезаем с *запроса*, чтобы получить
# инвариантную основу. Порядок важен — длинные первыми.
_ADJ_ENDINGS = re.compile(r"(ск|цк)(ии|ого|аго|ому|им|ом|ая|ои|ую|ие|их|ими)$")
_NOUN_ENDINGS = re.compile(r"(ов|ев|ин|ын)(а|у|ым|ом|е|ои|ы|ых|ыми)$")


def stem_query(surname: str):
    """Основа фамилии и позиции ненадёжных (дореформенных) букв в ней.

    Возвращает (основа, [индексы]). Отсечение окончания идёт с конца,
    поэтому индексы внутри оставшейся части не съезжают.
    """
    w, fragile = normalize_marked(surname)
    w = _ADJ_ENDINGS.sub(r"\1", w)
    w = _NOUN_ENDINGS.sub(r"\1", w)
    return w, [i for i in fragile if i < len(w)]


# Слово, разорванное переносом, даёт две половины. Если вторая испорчена
# (стёртая печать, курсив, соседняя числовая колонка), склейка бесполезна:
# токена с фамилией в тексте не возникнет. Но первая половина обычно цела,
# и она — начало фамилии. Так был пропущен 'Могу-' / 'чевъ' на стр. 208
# документа bv0000386.
# Дефис + пробел, а не только конец строки: в таблицах в ту же строку
# затекает соседняя колонка, и перенос перестаёт быть последним символом
# ("Могу- к |"). Внутрисловный дефис без пробела ("Штабсъ-Капитаны")
# под правило не подпадает.
_HYPHEN_END = re.compile(r"([А-Яа-яЁёѢѣІіЇїѲѳѴѵA-Za-z]{2,})[-‐‑–](?=[ \t]*\n|[ \t]+)")
PARTIAL_MAX_COST = 0.8
MIN_FRAGMENT = 4        # короче — слишком слабое свидетельство для обычного поиска


@dataclass
class Match:
    page: str          # идентификатор страницы/скана
    start: int         # смещение в исходном тексте страницы
    end: int
    raw: str           # как написано в документе
    cost: float        # накопленный штраф
    score: float       # 0..1, для сортировки
    context: str       # окружающий текст для глазной проверки
    partial: bool = False   # совпало только начало (слово разорвано переносом)


def find_in_text(text: str, surname: str, page: str = "-",
                 threshold: float = None, context_chars: int = 60,
                 min_fragment: int = MIN_FRAGMENT):
    """Ищет фамилию в одной странице текста.

    Токенизируем ИСХОДНЫЙ текст (чтобы сохранить смещения), нормализуем
    каждый токен по отдельности и сравниваем с основой запроса.
    """
    stem, fragile = stem_query(surname)
    if threshold is None:
        threshold = default_threshold(stem)

    original = text          # до склейки переносов
    text = dehyphenate(text)
    results = []
    for m in _TOKEN_RE.finditer(text):
        raw = m.group()
        norm = normalize(raw)
        if not norm:
            continue
        # быстрый отсев: длина не может отличаться сильнее, чем порог
        if len(norm) < len(stem) - threshold - 1:
            continue
        cost, _ = prefix_distance(stem, norm, fragile=fragile)
        if cost <= threshold:
            lo = max(0, m.start() - context_chars)
            hi = min(len(text), m.end() + context_chars)
            results.append(Match(
                page=page,
                start=m.start(),
                end=m.end(),
                raw=raw,
                cost=round(cost, 2),
                score=round(score(stem, norm, fragile=fragile), 3),
                context=" ".join(text[lo:hi].split()),
            ))
    results.extend(_find_hyphen_starts(original, stem, fragile, page,
                                       context_chars, min_fragment))
    results.sort(key=lambda r: (-r.score, r.start))
    return results


def _find_hyphen_starts(text, stem, fragile, page, context_chars, min_fragment):
    """Половинки фамилии, оставшиеся перед переносом строки.

    Обломок сравнивается как *начало* основы: 'Могу' против 'могучев'
    совпадает без штрафа, 'полу' или 'мага' — нет. Порог жёсткий: иначе
    каждое второе перенесённое слово попадёт в выдачу.
    """
    out = []
    for m in _HYPHEN_END.finditer(text):
        word = m.group(1)
        # Фамилия в печати всегда с прописной. Без этого условия правило
        # тонет в обычных словах: 'могу-щихъ', 'получе-нія', 'магази-на'
        # дают обломок, неотличимый от начала фамилии.
        if not word[:1].isupper():
            continue
        frag = normalize(word)
        if len(frag) < min_fragment or len(frag) >= len(stem):
            continue                      # целое слово ловится обычным путём
        cost, _ = prefix_distance(frag, stem, fragile=fragile)
        if cost > PARTIAL_MAX_COST:
            continue
        lo = max(0, m.start(1) - context_chars)
        hi = min(len(text), m.end(1) + context_chars)
        out.append(Match(
            page=page, start=m.start(1), end=m.end(1), raw=m.group(1) + "-",
            cost=round(cost, 2),
            score=round(max(0.0, 1.0 - cost / len(frag)) * 0.9, 3),
            context=" ".join(text[lo:hi].split()),
            partial=True))
    return out


def find_in_pages(pages, surname: str, **kw):
    """pages: итерируемое из (page_id, text)."""
    out = []
    for page_id, text in pages:
        out.extend(find_in_text(text, surname, page=page_id, **kw))
    # Сортировка по всему документу, а не внутри страниц: иначе лучшее
    # совпадение тонет среди шума с более ранних страниц.
    out.sort(key=lambda r: (-r.score, r.page, r.start))
    return out
