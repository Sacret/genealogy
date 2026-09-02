"""Нечёткое сравнение с учётом типичных ошибок OCR."""

# Пары букв, которые OCR путает систематически. Замена внутри пары
# стоит дешевле обычной (CONFUSION_COST вместо 1.0).
_CONFUSION_PAIRS = [
    # похожие по начертанию в печати
    ("н", "и"), ("н", "п"), ("и", "п"), ("н", "м"), ("н", "к"),
    ("е", "с"), ("о", "с"), ("о", "е"), ("о", "а"),
    ("ш", "щ"), ("ш", "ц"), ("ш", "т"), ("щ", "ц"),
    ("г", "т"), ("г", "р"), ("т", "ч"),
    ("л", "д"), ("л", "а"), ("д", "а"),
    ("б", "в"), ("б", "ъ"), ("в", "ь"),
    ("з", "в"), ("з", "э"), ("з", "с"),
    ("х", "к"), ("ж", "к"),
    ("у", "ц"), ("ф", "р"),
    # характерное для скорописи
    ("я", "и"), ("я", "н"), ("ы", "и"), ("ю", "н"),
]

CONFUSION_COST = 0.4

_CONFUSABLE = set()
for _a, _b in _CONFUSION_PAIRS:
    _CONFUSABLE.add((_a, _b))
    _CONFUSABLE.add((_b, _a))


def _sub_cost(a: str, b: str) -> float:
    if a == b:
        return 0.0
    if (a, b) in _CONFUSABLE:
        return CONFUSION_COST
    return 1.0


# Хвост фамилии несёт её идентичность: '-овъ' vs '-инъ' — разные роды,
# 'Кузнец' vs 'Кузнецовъ' — фамилия и ремесло. Ошибка в последних
# TAIL_LEN буквах штрафуется вдвое, ошибка в середине — почти всегда OCR.
TAIL_LEN = 3
TAIL_PENALTY = 2.0


# Удвоенная буква ('Гурѣевъ' -> 'Гуревъ') схлопывается OCR регулярно:
# два одинаковых глифа подряд сливаются. Пропуск такой буквы — не
# признак другого слова, штраф символический.
DOUBLE_COST = 0.3

# Буквы, отменённые реформой, OCR калечит непредсказуемо: ять уходит
# в 'б', 'в', 'т', фита в 'о'. Если в запросе они есть, мы знаем, какие
# именно позиции ненадёжны, и не штрафуем их по полной.
FRAGILE_COST = 0.4


def prefix_distance(pattern: str, word: str, max_tail: int = 5, fragile=()):
    """Расстояние pattern до *начала* word.

    Хвост word длиной до max_tail игнорируется бесплатно — так падежное
    окончание ('Кузнецов' + 'у', 'ымъ', 'ой') не штрафуется, и не нужен
    словарь окончаний.

    Возвращает (стоимость, сколько букв word поглощено).
    """
    n, m = len(pattern), len(word)
    if n == 0:
        return 0.0, 0

    fragile = set(fragile)
    prev = [float(j) for j in range(m + 1)]
    for i in range(1, n + 1):
        pi = pattern[i - 1]
        mult = TAIL_PENALTY if i > n - TAIL_LEN else 1.0
        doubled = (i > 1 and pattern[i - 2] == pi) or (i < n and pattern[i] == pi)
        skip = DOUBLE_COST if doubled else mult
        cur = [prev[0] + skip] + [0.0] * m
        for j in range(1, m + 1):
            if i - 1 in fragile:
                sub = FRAGILE_COST
            else:
                sub = _sub_cost(pi, word[j - 1]) * mult
            cur[j] = min(
                prev[j] + skip,           # пропуск буквы фамилии
                cur[j - 1] + 1.0,         # лишняя буква в слове
                prev[j - 1] + sub,
            )
        prev = cur

    lo = max(0, n - 2)
    hi = min(m, n + max_tail)
    best, best_j = min(((prev[j], j) for j in range(lo, hi + 1)), default=(float("inf"), 0))
    return best, best_j


def default_threshold(pattern: str) -> float:
    """Сколько ошибок терпим — зависит от длины фамилии.

    Короткая фамилия ('Дуб') при щедром пороге даст сотни ложных
    срабатываний, длинная при скупом — пропустит реальное вхождение.
    """
    n = len(pattern)
    if n <= 4:
        return 0.8
    if n <= 6:
        return 1.4
    if n <= 9:
        return 2.0
    return 2.6


def score(pattern: str, word: str, fragile=()) -> float:
    """0..1, где 1 — точное совпадение. Для сортировки выдачи."""
    d, _ = prefix_distance(pattern, word, fragile=fragile)
    return max(0.0, 1.0 - d / max(1, len(pattern)))
