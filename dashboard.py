"""
Lead Analytics Dashboard — интерактивная аналитика лидов.

Запуск: streamlit run dashboard.py

Логика вынесена в подмодули:
  dash_core.py     — конфиг/секреты, CRMS, загрузка Weeek/Trainity, auth, общая сводка
  dash_sections.py — render-секции (KPI, графики, когорты, регионы, дубли, источники)
"""

import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import streamlit as st
import pandas as pd

from dash_core import (
    CRMS, check_password, render_combined,
    load_deals, load_trainity, build_deals_df, extract_phone, dedupe_deals,
)
from dash_sections import (
    render_kpi, render_type_status_charts, render_source_loss,
    render_daily, render_cohort, render_regions, render_dups, render_source_table,
)

if not check_password():
    st.stop()

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
    show_combined = st.checkbox("📊 Общая сводка по воронкам")
    if show_combined:
        combined_funnels = st.multiselect(
            "Воронки для сводки",
            list(CRMS.keys()),
            default=list(CRMS.keys()),
            help="Кросс-сводка с дедупом по номеру между выбранными воронками",
        )
    crm_name = st.selectbox("CRM", list(CRMS.keys()))
    crm_cfg = CRMS[crm_name]

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

# Общая сводка по выбранным воронкам — отдельный экран
if show_combined:
    if not combined_funnels:
        st.warning("Выбери хотя бы одну воронку для сводки.")
        st.stop()
    render_combined(combined_funnels)
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
if date_from:
    cutoff = pd.Timestamp(date_from)
    created = pd.to_datetime(deals_df["Создано"], errors="coerce", utc=True).dt.tz_localize(None)
    total_before = len(deals_df)
    deals_df = deals_df[created >= cutoff].reset_index(drop=True)
    excluded = total_before - len(deals_df)
    st.sidebar.success(f"**{supply_choice}**\n\n{len(deals_df)} сделок (отсеяно {excluded} старых)")

# Сырые сделки (до дедупа) — для панели «Дубли».
deals_raw_df = deals_df
# Дедуп по телефону ПЕРЕД всеми метриками (1 запись на номер, самый продвинутый статус).
deals_df = dedupe_deals(deals_df)

# ── Render ──

render_kpi(deals_df)
st.divider()
render_type_status_charts(deals_df)
st.divider()
render_source_loss(deals_df)
st.divider()
render_daily(trainity_df, date_from)
render_cohort(trainity_df, deals_df)
st.divider()
render_regions(deals_df)
st.divider()
render_dups(deals_raw_df)
render_source_table(deals_df)

st.divider()
st.caption(f"Данные: Weeek CRM + Trainity | Обновлено: {datetime.now().strftime('%d.%m.%Y %H:%M')}")
