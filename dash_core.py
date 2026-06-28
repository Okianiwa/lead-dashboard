"""Ядро дашборда: конфиг/секреты, карта воронок, загрузка из Weeek/Trainity,
классификация, auth-gate и экран общей сводки.

На Streamlit Cloud (репо lead-dashboard) рядом должны лежать: dashboard.py,
dash_sections.py, status_classification.py, phone_info.py, def_codes.json.
"""

import os
import sys
import json
import time

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

import requests

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "lead_automator"))
sys.path.insert(0, _HERE)

from status_classification import (  # noqa: E402
    classify_status as _classify_key, normalize_phone, extract_phone,  # noqa: F401
    source_from_deal, dedupe_deals, SLA_WINDOWS_HOURS, POSITIVE_CLASSES,  # noqa: F401
)

WEEEK_BASE = "https://api.weeek.net/public/v1"
TRAINITY_API = "https://api.trainity.ru/table"


def get_secret(key, default=""):
    try:
        return st.secrets[key]
    except (KeyError, FileNotFoundError):
        return default


def get_config():
    return {
        "trainity_token": get_secret("TRAINITY_TOKEN"),
        "weeek_token_katey": get_secret("WEEEK_TOKEN_KATEY"),
        "weeek_token_split": get_secret("WEEEK_TOKEN_SPLIT"),
        "weeek_token_crm1": get_secret("WEEEK_TOKEN_CRM1"),
        "weeek_token_crm2": get_secret("WEEEK_TOKEN_CRM2"),
        "app_password": get_secret("APP_PASSWORD"),
    }


CFG = get_config()

TRAINITY_HEADERS = {
    "Content-Type": "application/json",
    "Authtoken": CFG["trainity_token"],
    "Origin": "https://excel.trainity.ru",
    "Referer": "https://excel.trainity.ru/",
}

CRMS = {
    "Общие с Катей": {
        "funnel_id": "wEJlnPv13erZjs0U",
        "token": CFG["weeek_token_katey"],
        "trainity_table_id": 228,
    },
    "Без Кати (50/50)": {
        "funnel_id": "yG4Sg8uJfbqeZ0ih",
        "token": CFG["weeek_token_split"],
        "trainity_table_id": 257,
    },
}
# Воронки Игоря/Марины — показываем только если задан токен в Secrets
# (WEEEK_TOKEN_CRM1 / WEEEK_TOKEN_CRM2). Источник у них — из самой сделки.
for _label, _fid, _tok in (
    ("Игорь (crm1)", "dJLJ7kBhz0Fd9pTB", CFG["weeek_token_crm1"]),
    ("Марина (crm2)", "meRR92YxY7hyvu5b", CFG["weeek_token_crm2"]),
):
    if _tok:
        CRMS[_label] = {"funnel_id": _fid, "token": _tok, "trainity_table_id": None}

COLOR_SUCCESS = "#2ecc71"
COLOR_IN_PROGRESS = "#1abc9c"
COLOR_NDZ = "#f39c12"
COLOR_TRASH = "#e74c3c"
COLOR_RESERVE = "#95a5a6"
COLOR_NEW = "#3498db"
COLOR_THINKING = "#9b59b6"

TYPE_COLORS = {
    "Успешный": COLOR_SUCCESS, "В процессе": COLOR_IN_PROGRESS,
    "НДЗ": COLOR_NDZ, "Брак": COLOR_TRASH,
    "Резерв": COLOR_RESERVE, "Новый": COLOR_NEW, "Думает": COLOR_THINKING, "Другое": "#bdc3c7"
}

# Класс из канонич. модуля -> метка дашборда (сохраняем привычный «В процессе»)
CLASS_TO_LABEL = {
    "success": "Успешный", "in_progress": "В процессе", "ndz": "НДЗ",
    "trash": "Брак", "thinking": "Думает", "new": "Новый", "other": "Другое",
}


def classify_status(name):
    """Имя статуса -> метка дашборда (через канонич. classify_status)."""
    return CLASS_TO_LABEL[_classify_key(name)]


# ── Data loading ──

@st.cache_data(ttl=604800)
def load_deals(funnel_id, token):
    """Load all deals from Weeek CRM."""
    statuses = requests.get(
        f"{WEEEK_BASE}/crm/funnels/{funnel_id}/statuses",
        headers={"Authorization": f"Bearer {token}"}, timeout=30
    ).json().get("statuses", [])

    all_deals = []
    for s in statuses:
        offset = 0
        while True:
            res = requests.get(
                f"{WEEEK_BASE}/crm/statuses/{s['id']}/deals",
                headers={"Authorization": f"Bearer {token}"},
                params={"limit": 100, "offset": offset}, timeout=30
            ).json()
            deals = res.get("deals", [])
            for d in deals:
                d["_status"] = s.get("name", "")
                d["_type"] = classify_status(s.get("name", ""))
            all_deals.extend(deals)
            if not res.get("hasMoreDeals") or not deals:
                break
            offset += 100
            time.sleep(0.15)
    return all_deals


@st.cache_data(ttl=604800)
def load_trainity(table_id):
    """Load leads from Trainity."""
    resp = requests.post(
        f"{TRAINITY_API}/take_data.php",
        data=json.dumps({"list": 1, "TableID": int(table_id)}),
        headers=TRAINITY_HEADERS, timeout=30
    )
    data = resp.json()["response"]["data"]
    if len(data) < 2:
        return pd.DataFrame()
    cols = data[0]
    rows = [r for r in data[1:] if any(c for c in r)]
    df = pd.DataFrame(rows, columns=cols, dtype=str)
    df = df.rename(columns={"Дата": "Дата", "id": "vid", "Телефон": "Телефон", "Сайт": "Источник", "Домен": "Комментарий"})
    df["_dt"] = pd.to_datetime(df["Дата"], errors="coerce")
    return df


def build_deals_df(deals):
    rows = []
    for d in deals:
        title = d.get("title", "")
        created = d.get("createdAt", "")
        try:
            dt = pd.to_datetime(created)
        except Exception:
            dt = pd.NaT
        rows.append({
            "Телефон": extract_phone(title),
            "Статус": d.get("_status", ""),
            "Тип": d.get("_type", ""),
            "Класс": _classify_key(d.get("_status", "")),
            # Источник берём из самой сделки (title-префикс / 'Источник:' в descr)
            "Источник": source_from_deal(title, d.get("description", "") or ""),
            "Создано": dt,
        })
    return pd.DataFrame(rows)


def check_password():
    """Returns True if password is correct or not configured."""
    app_pass = CFG["app_password"]
    if not app_pass:
        return True  # no password set = open access (local mode)

    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False

    if st.session_state.authenticated:
        return True

    st.set_page_config(page_title="Lead Analytics", page_icon="🔒", layout="centered")
    st.title("🔒 Lead Analytics")
    password = st.text_input("Пароль", type="password")
    if st.button("Войти", width="stretch"):
        if password == app_pass:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Неверный пароль")
    return False


def render_combined(selected_names=None):
    """Кросс-воронковая сводка с глобальным дедупом по номеру.

    selected_names: список названий воронок из CRMS. None/пусто → все воронки.
    """
    names = [n for n in (selected_names or CRMS) if n in CRMS]
    st.title("📊 Общая сводка по воронкам")
    st.caption("Воронки: " + ", ".join(names))
    frames = []
    with st.spinner("Загрузка воронок..."):
        for name in names:
            cfg = CRMS[name]
            df = dedupe_deals(build_deals_df(load_deals(cfg["funnel_id"], cfg["token"])))
            df = df[df["Телефон"].notna()].copy()
            if len(df) == 0:
                continue
            df["Воронка"] = name
            frames.append(df)

    if not frames:
        st.info("Нет данных по воронкам")
        return

    allf = pd.concat(frames, ignore_index=True)
    unique = dedupe_deals(allf, status_col="Статус")
    u_total = len(unique)
    u_success = int((unique["Класс"] == "success").sum())
    u_results = int(unique["Класс"].isin(POSITIVE_CLASSES).sum())
    overlap = int((allf.groupby("Телефон")["Воронка"].nunique() > 1).sum())
    conv = round(u_results / u_total * 100, 1) if u_total else 0
    conv_money = round(u_success / u_total * 100, 1) if u_total else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Уникальных номеров", u_total)
    c2.metric("Результативных", u_results, delta=f"{conv}%")
    c3.metric("Конверсия (с в работе)", f"{conv}%", help=f"только деньги: {conv_money}%")
    c4.metric("В неск. воронках", overlap, help="один номер в >1 воронке (race-дубли)")

    per = []
    for name in names:
        g = allf[allf["Воронка"] == name]
        if len(g) == 0:
            continue
        total = len(g)
        success = int((g["Класс"] == "success").sum())
        results = int(g["Класс"].isin(POSITIVE_CLASSES).sum())
        per.append({
            "Воронка": name, "Всего": total, "Результативных": results, "Успешных": success,
            "Конверсия %": round(results / total * 100, 1) if total else 0,
            "Конверсия деньги %": round(success / total * 100, 1) if total else 0,
        })
    per_df = pd.DataFrame(per)

    col_l, col_r = st.columns(2)
    with col_l:
        st.subheader("Конверсия по воронкам")
        fig = go.Figure()
        fig.add_trace(go.Bar(x=per_df["Воронка"], y=per_df["Конверсия %"],
                             name="С в работе", marker_color=COLOR_IN_PROGRESS,
                             text=per_df["Конверсия %"].apply(lambda x: f"{x}%"), textposition="outside"))
        fig.add_trace(go.Bar(x=per_df["Воронка"], y=per_df["Конверсия деньги %"],
                             name="Деньги", marker_color=COLOR_SUCCESS,
                             text=per_df["Конверсия деньги %"].apply(lambda x: f"{x}%"), textposition="outside"))
        fig.update_layout(barmode="group", height=400, margin=dict(l=10, r=10, t=30, b=10),
                          paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                          font=dict(color="#e2e8f0"), yaxis=dict(gridcolor="#2d3748", title="%"),
                          legend=dict(orientation="h", y=1.15))
        st.plotly_chart(fig, width="stretch")
    with col_r:
        st.subheader("По воронкам")
        st.dataframe(per_df, width="stretch", hide_index=True, height=380)
