#!/usr/bin/env python3
"""Проверка окружения перед долгим скачиванием и OCR."""

import shutil
import subprocess
import sys


MIN_PYTHON = (3, 10)


def inspect_environment(run=subprocess.run, which=shutil.which):
    """Возвращает пары (успех, сообщение), пригодные и для CLI, и для тестов."""
    checks = []
    version = sys.version_info[:3]
    checks.append((version >= MIN_PYTHON,
                   f"Python {'.'.join(map(str, version))} "
                   f"(нужно >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]})"))
    try:
        import PIL
        checks.append((True, f"Pillow {PIL.__version__}"))
    except ImportError:
        checks.append((False, "Pillow не установлен: python -m pip install -e ."))
    try:
        import numpy
        checks.append((True, f"NumPy {numpy.__version__}"))
    except ImportError:
        checks.append((False, "NumPy не установлен: python -m pip install -e ."))

    executable = which("tesseract")
    if not executable:
        checks.append((False, "Tesseract не найден в PATH"))
        return checks
    version_out = run([executable, "--version"], capture_output=True,
                      text=True, check=False)
    first = (version_out.stdout or version_out.stderr).splitlines()
    checks.append((version_out.returncode == 0,
                   first[0] if first else "Tesseract не отвечает"))
    langs_out = run([executable, "--list-langs"], capture_output=True,
                    text=True, check=False)
    languages = {line.strip() for line in langs_out.stdout.splitlines()[1:]}
    checks.append(("rus" in languages, "язык rus установлен" if "rus" in languages
                   else "нет языка rus для Tesseract"))
    # orus повышает качество старой орфографии в eval.py, но основной конвейер
    # работает на rus, поэтому отсутствие модели — предупреждение, не ошибка.
    checks.append((True, "язык orus установлен" if "orus" in languages
                   else "языка orus нет (необязателен, нужен только для сравнений)"))
    return checks


def main():
    checks = inspect_environment()
    for ok, message in checks:
        print(f"[{'ok' if ok else 'FAIL'}] {message}")
    return 0 if all(ok for ok, _ in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
