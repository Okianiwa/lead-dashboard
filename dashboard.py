"""
Lead Analytics Dashboard — интерактивная аналитика лидов.

Запуск: streamlit run dashboard.py
"""

import sys
import os
import json
import re
import time
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lead_automator"))

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests

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
        "trainity_token": get_secret("TRAINITY_TOKEN", "guestTokenApiWork12345678910111213141516171819202122232425262728"),
        "weeek_token_katey": get_secret("WEEEK_TOKEN_KATEY", "811677f3-08da-4128-a0a1-a616bc91cda5"),
        "weeek_token_split": get_secret("WEEEK_TOKEN_SPLIT", "81c37974-690c-495a-824b-432f08c67e53"),
        "app_password": get_secret("APP_PASSWORD", ""),
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

STATUS_SUCCESS = ["В работе от Игоря", "В работе", "Уехал", "Выплата прошла", "Ждём выплату", "Билеты куплены", "Обработан"]
STATUS_NDZ = ["НДЗ от Игоря", "ндз2 от Игоря"]
STATUS_TRASH = ["Брак от Игоря"]
STATUS_RESERVE = ["РЕЗЕРВ"]
STATUS_THINKING = ["Думает"]
STATUS_NEW = ["Новые лиды"]

COLOR_SUCCESS = "#2ecc71"
COLOR_NDZ = "#f39c12"
COLOR_TRASH = "#e74c3c"
COLOR_RESERVE = "#95a5a6"
COLOR_NEW = "#3498db"
COLOR_THINKING = "#9b59b6"

TYPE_COLORS = {
    "Успешный": COLOR_SUCCESS, "НДЗ": COLOR_NDZ, "Брак": COLOR_TRASH,
    "Резерв": COLOR_RESERVE, "Новый": COLOR_NEW, "Думает": COLOR_THINKING, "Другое": "#bdc3c7"
}


def classify_status(name):
    if name in STATUS_SUCCESS: return "Успешный"
    if name in STATUS_NDZ: return "НДЗ"
    if name in STATUS_TRASH: return "Брак"
    if name in STATUS_RESERVE: return "Резерв"
    if name in STATUS_THINKING: return "Думает"
    if name in STATUS_NEW: return "Новый"
    return "Другое"


def extract_phone(text):
    digits = re.sub(r"[^\d]", "", str(text))
    phones = re.findall(r"7\d{10}", digits)
    return phones[0] if phones else None


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
        phone = extract_phone(d.get("title", ""))
        created = d.get("createdAt", "")
        try:
            dt = pd.to_datetime(created)
        except:
            dt = pd.NaT
        rows.append({
            "Телефон": phone,
            "Статус": d.get("_status", ""),
            "Тип": d.get("_type", ""),
            "Создано": dt,
        })
    return pd.DataFrame(rows)


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
    crm_name = st.selectbox("CRM", list(CRMS.keys()))
    crm_cfg = CRMS[crm_name]

    use_date_filter = st.checkbox("Фильтр по дате поставки")
    date_from = None
    if use_date_filter:
        date_from = st.date_input("С даты", value=datetime.now().date() - timedelta(days=7))

    if st.button("🔄 Обновить данные", width="stretch"):
        st.cache_data.clear()

# ── Load data ──

with st.spinner("Загрузка данных из CRM..."):
    deals_raw = load_deals(crm_cfg["funnel_id"], crm_cfg["token"])
    trainity_df = load_trainity(crm_cfg["trainity_table_id"])

deals_df = build_deals_df(deals_raw)

# Phone → Source mapping from Trainity
phone_source = {}
if len(trainity_df) > 0:
    for _, row in trainity_df.iterrows():
        p = extract_phone(row.get("Телефон", ""))
        src = str(row.get("Источник", "")).strip()
        if p and src:
            phone_source[p] = src

deals_df["Источник"] = deals_df["Телефон"].map(phone_source).fillna("Неизвестен")

# Date filter
if date_from and len(trainity_df) > 0:
    cutoff = pd.Timestamp(date_from)
    filtered_phones = set()
    for _, row in trainity_df.iterrows():
        if pd.notna(row.get("_dt")) and row["_dt"] >= cutoff:
            p = extract_phone(row.get("Телефон", ""))
            if p:
                filtered_phones.add(p)
    deals_df = deals_df[deals_df["Телефон"].isin(filtered_phones)].reset_index(drop=True)
    st.sidebar.info(f"Поставка с {date_from}: **{len(deals_df)}** сделок")

# ── KPI Row ──

total = len(deals_df)
success = len(deals_df[deals_df["Тип"] == "Успешный"])
ndz = len(deals_df[deals_df["Тип"] == "НДЗ"])
trash = len(deals_df[deals_df["Тип"].isin(["Брак", "Резерв"])])
new_leads = len(deals_df[deals_df["Тип"] == "Новый"])
processed = total - new_leads
conv_total = round(success / total * 100, 1) if total else 0
conv_processed = round(success / processed * 100, 1) if processed else 0

col1, col2, col3, col4, col5, col6 = st.columns(6)
col1.metric("Всего сделок", total)
col2.metric("Обработано", processed, delta=f"{new_leads} новых")
col3.metric("Успешных", success, delta=f"{conv_total}%")
col4.metric("НДЗ", ndz)
col5.metric("Брак/Резерв", trash)
col6.metric("Конверсия обраб.", f"{conv_processed}%")

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
        stages = ["Всего", "→ Не обработано", "→ НДЗ", "→ Брак", "Успешные"]
        measures = ["absolute", "relative", "relative", "relative", "total"]
        vals = [total, -(total - processed), -ndz, -trash, 0]

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
                "Возраст (дней)": (datetime.now() - load_dt).days,
            })

    if cohort_rows:
        cohort_df = pd.DataFrame(cohort_rows)

        # Группируем по дате загрузки
        cohort_groups = cohort_df.groupby("Дата загрузки")
        cohort_stats = []
        for load_date, group in cohort_groups:
            total = len(group)
            age = group["Возраст (дней)"].iloc[0]
            processed = len(group[group["Тип"] != "Новый"])
            success = len(group[group["Тип"] == "Успешный"])
            ndz = len(group[group["Тип"] == "НДЗ"])
            trash = len(group[group["Тип"].isin(["Брак", "Резерв"])])
            new_count = len(group[group["Тип"] == "Новый"])

            cohort_stats.append({
                "Дата загрузки": load_date,
                "Всего": total,
                "Возраст дней": age,
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

        # SLA метрики
        recent = cohort_result[cohort_result["Возраст дней"] >= 1]
        if len(recent) > 0:
            avg_processed = recent["% обработано"].mean()
            day1 = recent[recent["Возраст дней"] >= 1]["% обработано"].mean()
            day3 = recent[recent["Возраст дней"] >= 3]["% обработано"].mean() if len(recent[recent["Возраст дней"] >= 3]) > 0 else 0
            day7 = recent[recent["Возраст дней"] >= 7]["% обработано"].mean() if len(recent[recent["Возраст дней"] >= 7]) > 0 else 0

            sla1, sla2, sla3 = st.columns(3)
            sla1.metric("SLA 1 день", f"{day1:.0f}%", help="Среднее % обработано через 1 день")
            sla2.metric("SLA 3 дня", f"{day3:.0f}%", help="Среднее % обработано через 3 дня")
            sla3.metric("SLA 7 дней", f"{day7:.0f}%", help="Среднее % обработано через 7 дней")

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

phones_with_data = deals_df.dropna(subset=["Телефон"])
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
        НДЗ=("Тип", lambda x: (x == "НДЗ").sum()),
        Брак=("Тип", lambda x: (x.isin(["Брак", "Резерв"])).sum()),
        Новых=("Тип", lambda x: (x == "Новый").sum()),
    ).reset_index()
    src_table["Конверсия %"] = (src_table["Успешных"] / src_table["Всего"] * 100).round(1)
    src_table = src_table.sort_values("Конверсия %", ascending=False)
    st.dataframe(src_table, width="stretch", hide_index=True)

# ── Footer ──

st.divider()
st.caption(f"Данные: Weeek CRM + Trainity | Обновлено: {datetime.now().strftime('%d.%m.%Y %H:%M')}")
