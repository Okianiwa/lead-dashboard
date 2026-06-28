"""
Канонический модуль классификации статусов Weeek + нормализации телефона.

ЕДИНЫЙ источник правды для: Statistics/crm_stats.py, Statistics/dashboard.py,
lead_automator/weeek_api.py, lead_automator/alerts.py. Раньше классификация
дублировалась в каждом файле и расходилась (Обработан=успех vs в работе,
ндз Казань терялась) — это давало разные цифры в CLI, дашборде и скоринге.

ZERO-dependency (только stdlib re + ленивый pandas в dedupe_deals) — модуль
копируется как есть в приватный репо lead-dashboard при деплое Streamlit.
"""

import re

# Таксономия выровнена по weeek_api.STATUS_CLASSES (драйвер скоринга):
# success = дошли до денег; in_progress = контакт есть, но не успех.
# Имена статусов выровнены по РЕАЛЬНЫМ воронкам Weeek (50/50 без Кати, crm1, crm2):
# у них разные названия одного смысла (НДЗ/НБТ/Нбт = не дозвонились;
# БРАК/Некачественные = брак; Подписал Контракт/Выплата прошла = win).
STATUS_SUCCESS = ["Билеты куплены", "Ждём выплату", "Выплата прошла", "Уехал",
                  "Подписал Контракт"]
STATUS_IN_PROGRESS = ["В работе от Игоря", "В работе", "В работе Казань", "Обработан",
                      "Наработки"]
STATUS_NDZ = ["НДЗ от Игоря", "ндз2 от Игоря", "ндз Казань", "НДЗ", "ндз2",
              "Автоответчик", "Временно для бота", "НБТ", "Нбт", "Нбт 2"]
STATUS_TRASH = ["Брак от Игоря", "РЕЗЕРВ", "БРАК", "Некачественные"]
STATUS_THINKING = ["Думает"]
STATUS_NEW = ["Новые лиды"]

# «Закрытые деньги» — реально полученные. Метрика «Деньги» считается ТОЛЬКО по
# ним. Остальные success-статусы (Уехал/Билеты куплены/Ждём выплату) — это «в
# сделке» (почти, но деньги ещё не пришли), их в «деньги» включать нельзя.
STATUS_MONEY = ["Выплата прошла", "Подписал Контракт"]


def is_money(status_name):
    """True только для реально закрытых денег (Выплата прошла)."""
    return (status_name or "").strip() in STATUS_MONEY

# class key -> русская метка типа (для отчётов/Excel)
CLASS_LABELS = {
    "success": "Успешный",
    "in_progress": "В работе",
    "ndz": "НДЗ",
    "trash": "Брак/Резерв",
    "thinking": "Думает",
    "new": "Новый",
    "other": "Другое",
}

# «Результативные» классы — лид доведён до результата (деньги ИЛИ активно в работе).
# Конверсия по умолчанию считается по ним: чистые «деньги» (success) дают слишком
# мелкий процент, т.к. большинство качественных лидов сидит в in_progress.
POSITIVE_CLASSES = ("success", "in_progress")

# Приоритет «продвинутости» статуса — при дублях телефона оставляем самый высокий.
STATUS_PRIORITY = {
    "success": 6,
    "in_progress": 5,
    "ndz": 4,
    "trash": 3,
    "thinking": 2,
    "new": 1,
    "other": 0,
}


def classify_status(status_name):
    """Имя статуса Weeek -> class key (success/in_progress/ndz/trash/thinking/new/other)."""
    s = (status_name or "").strip()
    if s in STATUS_SUCCESS:
        return "success"
    if s in STATUS_IN_PROGRESS:
        return "in_progress"
    if s in STATUS_NDZ:
        return "ndz"
    if s in STATUS_TRASH:
        return "trash"
    if s in STATUS_THINKING:
        return "thinking"
    if s in STATUS_NEW:
        return "new"
    return "other"


def status_label(status_name):
    """Имя статуса -> русская метка типа (Успешный/НДЗ/...)."""
    return CLASS_LABELS[classify_status(status_name)]


def normalize_phone(phone):
    """К формату 7XXXXXXXXXX. Зеркалит lead_automator/processor.normalize_phone без pandas."""
    if phone is None:
        return None
    digits = re.sub(r"[^\d]", "", str(phone).strip())
    if not digits:
        return None
    if digits.startswith("8") and len(digits) == 11:
        digits = "7" + digits[1:]
    elif len(digits) == 10:
        digits = "7" + digits
    if len(digits) != 11 or not digits.startswith("7"):
        return None
    return digits


def extract_phone(text):
    """Извлекает первый телефон из произвольного текста (title сделки), нормализует 8/7/10-значные.

    Раньше regex r'7\\d{10}' пропускал номера на 8 — теперь ловим [78]\\d{10}
    и 10-значные, потом нормализуем к 7XXXXXXXXXX.
    """
    digits = re.sub(r"[^\d]", "", str(text))
    m = re.search(r"[78]\d{10}", digits)
    if m:
        return normalize_phone(m.group(0))
    m = re.search(r"\d{10}", digits)
    if m:
        return normalize_phone(m.group(0))
    return None


# Источник зашит в сделку upload_leads тремя способами (lead_automator/weeek_api.py):
#   title  -> префикс [s1gnal.phones] / [s1gnal.sites]
#   descr  -> строка "Источник: {source}"
_SRC_DESC_RE = re.compile(r"Источник:\s*(.+)")


def source_from_deal(title="", description=""):
    """Достаёт источник прямо из сделки Weeek (без пере-качивания Trainity).

    Возвращает строку источника или None, если не удалось определить.
    """
    t = str(title or "")
    if "[s1gnal.phones]" in t:
        return "s1gnal.phones"
    if "[s1gnal.sites]" in t:
        return "s1gnal.sites"
    m = _SRC_DESC_RE.search(str(description or ""))
    if m:
        src = m.group(1).strip()
        if src and src.lower() != "неизвестен":
            return src
    return None


# Домен сайта-донора зашит в сделку двумя способами (см. скрины Weeek):
#   s1gnal.sites  -> "Источник: site.ru"            (домен прямо в Источнике)
#   s1gnal.phones -> "URL донора: https://site/..." (домен в URL донора)
_URL_DONOR_RE = re.compile(r"URL\s*донора:\s*(\S+)", re.IGNORECASE)
_HOST_RE = re.compile(r"^(?:https?://)?(?:www\.)?([^/\s:?#]+)", re.IGNORECASE)


def _to_domain(s):
    """Из URL или строки достаёт домен. None если не похоже на домен (телефон/пусто)."""
    s = str(s or "").strip().strip('"\'<>')
    if not s or s.lower() in ("nan", "none", "неизвестен"):
        return None
    m = _HOST_RE.match(s)
    if not m:
        return None
    host = m.group(1).lower().rstrip(".")
    # домен = есть точка И хотя бы один не-цифровой символ (отсекаем телефоны)
    if "." not in host or not re.search(r"[^\d.]", host):
        return None
    return host


def site_from_deal(title="", description=""):
    """Домен сайта-донора из сделки. None если не определить.

    Сначала пробуем 'URL донора:' (s1gnal.phones), потом 'Источник:' (s1gnal.sites).
    """
    d = str(description or "")
    m = _URL_DONOR_RE.search(d)
    if m:
        dom = _to_domain(m.group(1))
        if dom:
            return dom
    m = _SRC_DESC_RE.search(d)
    if m:
        dom = _to_domain(m.group(1).split("|")[0])
        if dom:
            return dom
    return None


SLA_WINDOWS_HOURS = (24, 72, 168)  # «1/3/7 дней» в часах (граница строго >=)


def sla_windows_met(age_hours, windows=SLA_WINDOWS_HOURS):
    """Какие SLA-окна (в часах) когорта данного возраста уже прожила.

    Считаем в ЧАСАХ, а не .days — иначе лид возрастом 25ч ошибочно «целиком в
    срок 1 день». age_hours=25 -> [24] (24ч прожиты, 72/168 — нет).
    """
    try:
        a = float(age_hours)
    except (TypeError, ValueError):
        return []
    return [w for w in windows if a >= w]


def dedupe_deals(df, phone_col="Телефон", status_col="Статус"):
    """Оставляет одну сделку на нормализованный телефон — с самым продвинутым статусом.

    Строки без распознанного телефона сохраняются как есть (дедупить нечем).
    Применяется ПЕРЕД подсчётом метрик, чтобы дубли не раздували знаменатель конверсии.
    """
    if df is None or len(df) == 0:
        return df
    import pandas as pd

    d = df.copy()
    d["_pn"] = d[phone_col].map(lambda x: normalize_phone(x) if x is not None else None)
    d["_prio"] = d[status_col].map(lambda s: STATUS_PRIORITY.get(classify_status(s), 0))

    no_phone = d[d["_pn"].isna()]
    has_phone = d[d["_pn"].notna()]
    has_phone = (
        has_phone.sort_values("_prio", ascending=False)
        .drop_duplicates(subset="_pn", keep="first")
    )
    out = pd.concat([has_phone, no_phone], ignore_index=True)
    return out.drop(columns=["_pn", "_prio"])
