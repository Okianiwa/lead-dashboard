"""
Lead Analytics Dashboard — интерактивная аналитика лидов.

Запуск: streamlit run dashboard.py
"""

import sys
import os
import json
import time
from datetime import datetime, timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "lead_automator"))
sys.path.insert(0, _HERE)

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests

# Канонический модуль (единый источник правды). На Streamlit Cloud (отдельный
# приватный репо lead-dashboard) status_classification.py должен лежать рядом
# с dashboard.py — копировать при деплое.
from status_classification import (  # noqa: E402
    classify_status as _classify_key, normalize_phone, extract_phone,  # noqa: F401
    source_from_deal, dedupe_deals, SLA_WINDOWS_HOURS,
    POSITIVE_CLASSES,
)

# ── Config (tokens from st.secrets or defaults for local run) ──

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


def render_combined():
    """Общая кросс-воронковая сводка по ВСЕМ воронкам (глобальный дедуп).
    Inline (не импортирует crm_stats — на Streamlit Cloud его нет)."""
    st.title("📊 Общая сводка по всем воронкам")
    frames = []
    with st.spinner("Загрузка всех воронок..."):
        for name, cfg in CRMS.items():
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
    for name in CRMS:
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


# ── Auth gate ──

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


if not check_password():
    st.stop()

# ── Page config ──

st.set_page_config(page_title="Lead Analytics", page_icon="📊", layout="wide")

st.markdown("""
<style>
    .block-container { padding-top: 1rem; }
    [data-testid="stMetric"] {
        background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
        border: 1px solid #0f3460;
        border-radius: 12px;
        padding: 16px 20px;
    }
    [data-testid="stMetric"] label { color: #a0aec0 !important; font-size: 0.85rem !important; }
    [data-testid="stMetric"] [data-testid="stMetricValue"] { color: #e2e8f0 !important; font-size: 1.8rem !important; }
    [data-testid="stMetric"] [data-testid="stMetricDelta"] { font-size: 0.9rem !important; }
</style>
""", unsafe_allow_html=True)

st.title("📊 Lead Analytics Dashboard")

# ── Sidebar ──

with st.sidebar:
    st.header("Настройки")
    show_combined = st.checkbox("📊 Общая сводка (все воронки)")
    crm_name = st.selectbox("CRM", list(CRMS.keys()))
    crm_cfg = CRMS[crm_name]

    # Фильтр по поставке
    SUPPLY_PRESETS = {
        "Все данные": None,
        "Авито-поставка (с 30.01.2026)": datetime(2026, 1, 30).date(),
        "Своя дата...": "custom",
    }
    supply_choice = st.selectbox("Поставка", list(SUPPLY_PRESETS.keys()), index=1)
    date_from = SUPPLY_PRESETS[supply_choice]
    if date_from == "custom":
        date_from = st.date_input("С даты", value=datetime.now().date() - timedelta(days=7))

    if st.button("🔄 Обновить данные", width="stretch"):
        st.cache_data.clear()

# Общая сводка по всем воронкам — отдельный экран
if show_combined:
    render_combined()
    st.stop()

# ── Load data ──

with st.spinner("Загрузка данных из CRM..."):
    deals_raw = load_deals(crm_cfg["funnel_id"], crm_cfg["token"])
    _tid = crm_cfg.get("trainity_table_id")
    trainity_df = load_trainity(_tid) if _tid else pd.DataFrame()

deals_df = build_deals_df(deals_raw)

# Источник: сначала из самой сделки (deals_df['Источник']), для пустых — добор
# из Trainity (только split/Катя; у crm1/crm2 Trainity нет).
if len(trainity_df) > 0:
    phone_source = {}
    for _, row in trainity_df.iterrows():
        p = extract_phone(row.get("Телефон", ""))
        src = str(row.get("Источник", "")).strip()
        if p and src and p not in phone_source:  # первое вхождение
            phone_source[p] = src
    mask = deals_df["Источник"].isna()
    deals_df.loc[mask, "Источник"] = deals_df.loc[mask, "Телефон"].map(phone_source)
deals_df["Источник"] = deals_df["Источник"].fillna("Неизвестен")

# Фильтр по поставке = по дате СОЗДАНИЯ сделки (createdAt), tz-safe.
# Раньше фильтровали по телефонам Trainity → старые сделки протекали и для
# crm1/crm2 (без Trainity) фильтр вообще не работал.
if date_from:
    cutoff = pd.Timestamp(date_from)
    created = pd.to_datetime(deals_df["Создано"], errors="coerce", utc=True).dt.tz_localize(None)
    total_before = len(deals_df)
    deals_df = deals_df[created >= cutoff].reset_index(drop=True)
    excluded = total_before - len(deals_df)
    st.sidebar.success(f"**{supply_choice}**\n\n{len(deals_df)} сделок (отсеяно {excluded} старых)")

# Сырые сделки (до дедупа) — для панели «Дубли»: после дедупа в deals_df
# остаётся 1 запись на номер, поэтому дубли надо искать ДО схлопывания.
deals_raw_df = deals_df

# Дедуп по телефону ПЕРЕД всеми метриками (один лид с несколькими сделками
# раздувал знаменатель конверсии и когорты). Оставляем самый продвинутый статус.
deals_df = dedupe_deals(deals_df)

# ── KPI Row ──

total = len(deals_df)
success = len(deals_df[deals_df["Тип"] == "Успешный"])
in_progress = len(deals_df[deals_df["Тип"] == "В процессе"])
ndz = len(deals_df[deals_df["Тип"] == "НДЗ"])
trash = len(deals_df[deals_df["Тип"].isin(["Брак", "Резерв"])])
new_leads = len(deals_df[deals_df["Тип"] == "Новый"])
processed = total - new_leads
results = success + in_progress  # результативные: деньги + в работе
conv_total = round(results / total * 100, 1) if total else 0
conv_processed = round(results / processed * 100, 1) if processed else 0
conv_money = round(success / total * 100, 1) if total else 0

ndz_pct = round(ndz / total * 100, 1) if total else 0
trash_pct = round(trash / total * 100, 1) if total else 0

col1, col2, col3, col4, col5, col6, col7 = st.columns(7)
col1.metric("Всего", total)
col2.metric("Успешных (деньги)", success, delta=f"{conv_money}%")
col3.metric("В работе", in_progress)
col4.metric("НДЗ", ndz, delta=f"{ndz_pct}%", delta_color="inverse")
col5.metric("Брак/Резерв", trash, delta=f"{trash_pct}%", delta_color="inverse")
col6.metric("Новых", new_leads)
col7.metric("Конверсия (с в работе)", f"{conv_total}%", help=f"только деньги: {conv_money}%")

st.divider()

# ── Charts Row 1 ──

col_left, col_right = st.columns(2)

with col_left:
    st.subheader("Воронка по типам")
    type_counts = deals_df["Тип"].value_counts().reset_index()
    type_counts.columns = ["Тип", "Кол-во"]
    order = ["Успешный", "НДЗ", "Думает", "Брак", "Резерв", "Новый", "Другое"]
    type_counts["_order"] = type_counts["Тип"].apply(lambda x: order.index(x) if x in order else 99)
    type_counts = type_counts.sort_values("_order")
    colors = [TYPE_COLORS.get(t, "#bdc3c7") for t in type_counts["Тип"]]

    fig_funnel = go.Figure(go.Funnel(
        y=type_counts["Тип"],
        x=type_counts["Кол-во"],
        marker=dict(color=colors),
        textinfo="value+percent total",
        textfont=dict(size=14),
    ))
    fig_funnel.update_layout(
        height=400, margin=dict(l=10, r=10, t=10, b=10),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#e2e8f0"),
    )
    st.plotly_chart(fig_funnel, width="stretch")

with col_right:
    st.subheader("Распределение статусов")
    status_counts = deals_df["Статус"].value_counts().reset_index()
    status_counts.columns = ["Статус", "Кол-во"]

    fig_pie = px.pie(
        status_counts, values="Кол-во", names="Статус",
        color="Статус",
        color_discrete_map={s: TYPE_COLORS.get(classify_status(s), "#bdc3c7") for s in status_counts["Статус"]},
        hole=0.4,
    )
    fig_pie.update_layout(
        height=400, margin=dict(l=10, r=10, t=10, b=10),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#e2e8f0"),
        legend=dict(font=dict(size=11)),
    )
    fig_pie.update_traces(textinfo="percent+value", textfont_size=12)
    st.plotly_chart(fig_pie, width="stretch")

st.divider()

# ── Charts Row 2 ──

col_left2, col_right2 = st.columns(2)

with col_left2:
    st.subheader("Конверсия по источникам")
    if len(deals_df) > 0:
        src_stats = deals_df.groupby("Источник").agg(
            Всего=("Тип", "count"),
            Успешных=("Тип", lambda x: (x == "Успешный").sum()),
            НДЗ=("Тип", lambda x: (x == "НДЗ").sum()),
        ).reset_index()
        src_stats["Конверсия %"] = (src_stats["Успешных"] / src_stats["Всего"] * 100).round(1)
        src_stats = src_stats.sort_values("Конверсия %", ascending=True)

        fig_src = go.Figure()
        fig_src.add_trace(go.Bar(
            y=src_stats["Источник"], x=src_stats["Успешных"],
            name="Успешных", orientation="h", marker_color=COLOR_SUCCESS,
        ))
        fig_src.add_trace(go.Bar(
            y=src_stats["Источник"], x=src_stats["НДЗ"],
            name="НДЗ", orientation="h", marker_color=COLOR_NDZ,
        ))
        fig_src.add_trace(go.Bar(
            y=src_stats["Источник"], x=src_stats["Всего"] - src_stats["Успешных"] - src_stats["НДЗ"],
            name="Остальное", orientation="h", marker_color="#95a5a6",
        ))
        fig_src.update_layout(
            barmode="stack", height=max(300, len(src_stats) * 35),
            margin=dict(l=10, r=10, t=10, b=10),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#e2e8f0"),
            legend=dict(orientation="h", y=-0.1),
            xaxis=dict(gridcolor="#2d3748"),
            yaxis=dict(gridcolor="#2d3748"),
        )
        st.plotly_chart(fig_src, width="stretch")

with col_right2:
    st.subheader("Потери на этапах")
    if processed > 0:
        thinking = len(deals_df[deals_df["Тип"] == "Думает"])
        other = processed - success - ndz - trash - thinking
        stages = ["Всего", "→ Новые", "→ НДЗ", "→ Брак/Резерв", "→ Думает"]
        measures = ["absolute", "relative", "relative", "relative", "relative"]
        vals = [total, -(total - processed), -ndz, -trash, -thinking]
        if other > 0:
            stages.append("→ Другое")
            measures.append("relative")
            vals.append(-other)
        stages.append("Успешные")
        measures.append("total")
        vals.append(0)

        fig_loss = go.Figure(go.Waterfall(
            x=stages, y=vals,
            measure=measures,
            connector=dict(line=dict(color="#4a5568", width=1)),
            decreasing=dict(marker=dict(color="#e74c3c")),
            increasing=dict(marker=dict(color="#2ecc71")),
            totals=dict(marker=dict(color=COLOR_SUCCESS)),
            textposition="outside",
            textfont=dict(color="#e2e8f0", size=13),
        ))
        fig_loss.update_layout(
            height=400, margin=dict(l=10, r=10, t=10, b=10),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#e2e8f0"),
            xaxis=dict(gridcolor="#2d3748"),
            yaxis=dict(gridcolor="#2d3748", title="Лидов"),
            showlegend=False,
        )
        st.plotly_chart(fig_loss, width="stretch")

st.divider()

# ── Daily dynamics ──

st.subheader("📈 Динамика по дням")

if len(trainity_df) > 0 and "_dt" in trainity_df.columns:
    daily = trainity_df.copy()
    daily["Дата_день"] = daily["_dt"].dt.date

    if date_from:
        daily = daily[daily["_dt"] >= pd.Timestamp(date_from)]

    daily_counts = daily.groupby("Дата_день").size().reset_index(name="Новых лидов")
    daily_counts["Дата_день"] = pd.to_datetime(daily_counts["Дата_день"])

    fig_daily = px.area(
        daily_counts, x="Дата_день", y="Новых лидов",
        color_discrete_sequence=[COLOR_NEW],
    )
    fig_daily.update_layout(
        height=300, margin=dict(l=10, r=10, t=10, b=10),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#e2e8f0"),
        xaxis=dict(gridcolor="#2d3748", title=""),
        yaxis=dict(gridcolor="#2d3748", title="Лидов"),
    )
    st.plotly_chart(fig_daily, width="stretch")

# ── Cohort Analysis ──

st.subheader("📅 Когортный анализ")
st.caption("Лиды, загруженные в определённый день — какой % обработан через 1/3/7 дней?")

if len(trainity_df) > 0 and "_dt" in trainity_df.columns and len(deals_df) > 0:
    # Маппинг телефон → дата загрузки из Trainity
    phone_load_date = {}
    for _, row in trainity_df.iterrows():
        p = extract_phone(row.get("Телефон", ""))
        dt = row.get("_dt")
        if p and pd.notna(dt):
            phone_load_date[p] = dt

    # Для каждой сделки: дата загрузки + текущий тип
    cohort_rows = []
    for _, row in deals_df.iterrows():
        phone = row.get("Телефон")
        if phone and phone in phone_load_date:
            load_dt = phone_load_date[phone]
            cohort_rows.append({
                "Дата загрузки": load_dt.date(),
                "Тип": row["Тип"],
                "Возраст (ч)": (datetime.now() - load_dt).total_seconds() / 3600.0,
            })

    if cohort_rows:
        cohort_df = pd.DataFrame(cohort_rows)

        # Группируем по дате загрузки
        cohort_groups = cohort_df.groupby("Дата загрузки")
        cohort_stats = []
        for load_date, group in cohort_groups:
            total = len(group)
            age_h = group["Возраст (ч)"].iloc[0]
            # «Обработано» = доведено до результата (исключаем ещё «Думает»)
            processed = len(group[group["Тип"].isin(["Успешный", "В процессе", "НДЗ", "Брак", "Резерв"])])
            success = len(group[group["Тип"] == "Успешный"])
            ndz = len(group[group["Тип"] == "НДЗ"])
            trash = len(group[group["Тип"].isin(["Брак", "Резерв"])])
            new_count = len(group[group["Тип"] == "Новый"])

            cohort_stats.append({
                "Дата загрузки": load_date,
                "Всего": total,
                "Возраст ч": round(age_h, 1),
                "Возраст дней": round(age_h / 24, 1),
                "Обработано": processed,
                "% обработано": round(processed / total * 100, 1) if total else 0,
                "Успешных": success,
                "% конверсия": round(success / total * 100, 1) if total else 0,
                "НДЗ": ndz,
                "Брак": trash,
                "Ещё новых": new_count,
            })

        cohort_result = pd.DataFrame(cohort_stats).sort_values("Дата загрузки", ascending=False)

        # График: % обработано по когортам
        col_coh1, col_coh2 = st.columns(2)

        with col_coh1:
            fig_coh = go.Figure()
            fig_coh.add_trace(go.Bar(
                x=cohort_result["Дата загрузки"].astype(str),
                y=cohort_result["% обработано"],
                name="% обработано",
                marker_color=COLOR_NDZ,
                text=cohort_result["% обработано"].apply(lambda x: f"{x}%"),
                textposition="outside",
                textfont=dict(color="#e2e8f0"),
            ))
            fig_coh.add_trace(go.Bar(
                x=cohort_result["Дата загрузки"].astype(str),
                y=cohort_result["% конверсия"],
                name="% конверсия",
                marker_color=COLOR_SUCCESS,
                text=cohort_result["% конверсия"].apply(lambda x: f"{x}%"),
                textposition="outside",
                textfont=dict(color="#e2e8f0"),
            ))
            fig_coh.update_layout(
                barmode="group",
                height=400, margin=dict(l=10, r=10, t=30, b=10),
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#e2e8f0"),
                xaxis=dict(gridcolor="#2d3748", title="Дата загрузки"),
                yaxis=dict(gridcolor="#2d3748", title="%", range=[0, 105]),
                legend=dict(orientation="h", y=1.1),
            )
            st.plotly_chart(fig_coh, width="stretch")

        with col_coh2:
            st.dataframe(cohort_result, width="stretch", hide_index=True, height=380)

        # SLA метрики — в ЧАСАХ (24/72/168ч). Когорта возрастом 25ч попадает
        # только в окно ≥24ч, не в ≥72ч (раньше .days давал off-by-one).
        w1, w3, w7 = SLA_WINDOWS_HOURS
        c1 = cohort_result[cohort_result["Возраст ч"] >= w1]
        if len(c1) > 0:
            c3 = cohort_result[cohort_result["Возраст ч"] >= w3]
            c7 = cohort_result[cohort_result["Возраст ч"] >= w7]
            day1 = c1["% обработано"].mean()
            day3 = c3["% обработано"].mean() if len(c3) > 0 else 0
            day7 = c7["% обработано"].mean() if len(c7) > 0 else 0

            sla1, sla2, sla3 = st.columns(3)
            sla1.metric("SLA 1 день (≥24ч)", f"{day1:.0f}%", help="Среднее % обработано среди когорт старше 24ч")
            sla2.metric("SLA 3 дня (≥72ч)", f"{day3:.0f}%", help="Среднее % обработано среди когорт старше 72ч")
            sla3.metric("SLA 7 дней (≥168ч)", f"{day7:.0f}%", help="Среднее % обработано среди когорт старше 168ч")

st.divider()

# ── Regions ──

st.subheader("🗺 Лиды по регионам")

try:
    from phone_info import get_phone_info, get_region_short

    phones_for_regions = deals_df.dropna(subset=["Телефон"])
    if len(phones_for_regions) > 0:
        phones_for_regions = phones_for_regions.copy()
        phones_for_regions["Регион"] = phones_for_regions["Телефон"].apply(
            lambda p: get_region_short(get_phone_info(p)["region"])
        )

        col_rg1, col_rg2 = st.columns(2)

        with col_rg1:
            region_counts = phones_for_regions["Регион"].value_counts().reset_index()
            region_counts.columns = ["Регион", "Кол-во"]
            fig_rg = px.pie(region_counts.head(15), values="Кол-во", names="Регион", hole=0.35)
            fig_rg.update_layout(
                height=400, margin=dict(l=10, r=10, t=10, b=10),
                paper_bgcolor="rgba(0,0,0,0)", font=dict(color="#e2e8f0"),
                legend=dict(font=dict(size=10)),
            )
            fig_rg.update_traces(textinfo="percent+value", textfont_size=11)
            st.plotly_chart(fig_rg, width="stretch")

        with col_rg2:
            # Конверсия по регионам
            rg_stats = phones_for_regions.groupby("Регион").agg(
                Всего=("Тип", "count"),
                Успешных=("Тип", lambda x: (x == "Успешный").sum()),
            ).reset_index()
            rg_stats["Конверсия %"] = (rg_stats["Успешных"] / rg_stats["Всего"] * 100).round(1)
            rg_stats = rg_stats.sort_values("Всего", ascending=False).head(15)
            st.dataframe(rg_stats, width="stretch", hide_index=True, height=380)
except ImportError:
    st.info("Модуль phone_info не найден — регионы недоступны на Streamlit Cloud")

st.divider()

# ── Duplicates ──

st.subheader("🔍 Дубли номеров")

phones_with_data = deals_raw_df.dropna(subset=["Телефон"])
phone_counts = phones_with_data["Телефон"].value_counts()
dup_phones = phone_counts[phone_counts > 1]

if len(dup_phones) > 0:
    st.warning(f"Найдено **{len(dup_phones)}** дублированных номеров ({dup_phones.sum() - len(dup_phones)} лишних записей)")
    dup_detail = phones_with_data[phones_with_data["Телефон"].isin(dup_phones.index)].sort_values(["Телефон", "Создано"])
    st.dataframe(dup_detail, width="stretch", height=300)
else:
    st.success("Дублей не найдено!")

# ── Source table ──

st.subheader("📋 Таблица по источникам")

if len(deals_df) > 0:
    src_table = deals_df.groupby("Источник").agg(
        Всего=("Тип", "count"),
        Успешных=("Тип", lambda x: (x == "Успешный").sum()),
        ВРаботе=("Тип", lambda x: (x == "В процессе").sum()),
        НДЗ=("Тип", lambda x: (x == "НДЗ").sum()),
        Брак=("Тип", lambda x: (x.isin(["Брак", "Резерв"])).sum()),
        Новых=("Тип", lambda x: (x == "Новый").sum()),
    ).reset_index()
    src_table["Результативных"] = src_table["Успешных"] + src_table["ВРаботе"]
    src_table["Конверсия %"] = (src_table["Результативных"] / src_table["Всего"] * 100).round(1)
    src_table["Конверсия деньги %"] = (src_table["Успешных"] / src_table["Всего"] * 100).round(1)
    src_table = src_table.rename(columns={"ВРаботе": "В работе"})
    src_table = src_table.sort_values("Конверсия %", ascending=False)
    st.dataframe(src_table, width="stretch", hide_index=True)

# ── Footer ──

st.divider()
st.caption(f"Данные: Weeek CRM + Trainity | Обновлено: {datetime.now().strftime('%d.%m.%Y %H:%M')}")
