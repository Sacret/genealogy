"""Метрика, которая имеет значение: сколько фамилий со страницы
находит матчер в OCR-выдаче. Не CER — распознавание может калечить
цифры и предлоги, лишь бы фамилии оставались опознаваемыми."""

import subprocess, sys, pathlib
from surnamefind.search import find_in_text

# Вычитано глазами со скана стр. 20
TRUTH = [
    "Рыковскій", "Байдалаковъ", "Сагацкій", "Балабинъ", "Малюгинъ",
    "Зеленковъ", "Павловъ", "Серебряковъ", "Бакбушевъ", "Епифановъ",
    "Шебановъ", "Криковъ", "Кирѣевъ", "Гурѣевъ", "Гудковъ", "Садчиковъ",
    "Дьяченковъ", "Бородинъ",
]


def ocr(img, lang="rus", psm="4", extra=()):
    cmd = ["tesseract", str(img), "-", "-l", lang, "--psm", psm, *extra]
    return subprocess.run(cmd, capture_output=True, text=True).stdout


def evaluate(text, label):
    hit, miss = [], []
    for s in TRUTH:
        (hit if find_in_text(text, s) else miss).append(s)
    print(f"{label:26} {len(hit):2d}/{len(TRUTH)}  пропущены: {', '.join(miss) or '—'}")
    return len(hit)


if __name__ == "__main__":
    for dpi in (150, 300, 400, 600):
        img = pathlib.Path(f"bench/p020_{dpi}.jpg")
        if img.exists():
            evaluate(ocr(img), f"rus psm4 @{dpi}dpi")

    print()
    for lang in ("rus", "orus", "orus+rus"):
        for psm in ("4", "6"):
            t = ocr("bench/p020_400.jpg", lang=lang, psm=psm)
            evaluate(t, f"{lang} psm{psm} @400dpi")
