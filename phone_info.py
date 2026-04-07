"""
Определение региона и оператора по номеру телефона.
База: DEF-коды Россвязи (52,626 записей) из github.com/antirek/numcap.
Файл def_codes.json загружается при первом запуске.
"""

import os
import json
import logging

import requests

logger = logging.getLogger(__name__)

DEF_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "def_codes.json")
DEF_URL = "https://raw.githubusercontent.com/antirek/numcap/master/data/Kody_DEF-9kh.json"

_cache = {}


def _download_def_codes():
    """Скачивает и конвертирует базу DEF-кодов в range-based lookup."""
    logger.info("Загрузка базы DEF-кодов Россвязи...")
    resp = requests.get(DEF_URL, timeout=60)
    data = resp.json()
    entries = [d for d in data if d.get("code", "").isdigit()]

    by_code = {}
    for e in entries:
        code = e["code"]
        if code not in by_code:
            by_code[code] = []
        by_code[code].append([
            int(e["begin"]), int(e["end"]),
            e["operator"][:60], e["region"][:60],
        ])

    for code in by_code:
        by_code[code].sort()

    with open(DEF_FILE, "w", encoding="utf-8") as f:
        json.dump(by_code, f, ensure_ascii=False, separators=(",", ":"))

    logger.info(f"Сохранено: {len(by_code)} кодов, {len(entries)} диапазонов")
    return by_code


def _load_def_codes():
    """Загружает базу DEF-кодов (скачивает если нет)."""
    global _cache
    if _cache:
        return _cache

    if not os.path.exists(DEF_FILE):
        _cache = _download_def_codes()
    else:
        with open(DEF_FILE, "r", encoding="utf-8") as f:
            _cache = json.load(f)

    return _cache


def get_phone_info(phone):
    """
    Возвращает информацию о номере телефона.
    phone: '7XXXXXXXXXX' (11 цифр)
    Returns: dict {region, operator}
    """
    if not phone or len(phone) != 11 or not phone.startswith("7"):
        return {"region": "Неизвестно", "operator": "Неизвестно"}

    code = phone[1:4]
    try:
        local = int(phone[4:11])
    except ValueError:
        return {"region": "Неизвестно", "operator": "Неизвестно"}

    db = _load_def_codes()
    ranges = db.get(code, [])

    for entry in ranges:
        begin, end = entry[0], entry[1]
        if begin <= local <= end:
            return {"region": entry[3], "operator": entry[2]}

    return {"region": "Неизвестно", "operator": "Неизвестно"}


def get_region_short(region):
    """Сокращает название региона для удобства."""
    if not region:
        return "Неизвестно"

    # Нормализация вариантов Москвы
    r = region.lower()
    if "москв" in r:
        return "Москва/МО"
    if "санкт-петербург" in r or "ленинград" in r:
        return "СПб/ЛО"

    shortcuts = {
        "Краснодарский край": "Краснодар",
        "Ростовская обл.": "Ростов",
        "Нижегородская обл.": "Нижний Новгород",
        "Свердловская обл.": "Екатеринбург",
        "Челябинская обл.": "Челябинск",
        "Новосибирская обл.": "Новосибирск",
        "Самарская обл.": "Самара",
        "Республика Татарстан": "Казань",
        "Республика Башкортостан": "Уфа",
        "Волгоградская обл.": "Волгоград",
        "Воронежская обл.": "Воронеж",
        "Красноярский край": "Красноярск",
        "Пермский край": "Пермь",
        "Ставропольский край": "Ставрополь",
        "Республика Крым": "Крым",
        "Ростовская область": "Ростов",
        "Нижегородская область": "Нижний Новгород",
        "Свердловская область": "Екатеринбург",
        "Челябинская область": "Челябинск",
        "Новосибирская область": "Новосибирск",
        "Самарская область": "Самара",
        "Волгоградская область": "Волгоград",
        "Воронежская область": "Воронеж",
    }
    return shortcuts.get(region, region)
