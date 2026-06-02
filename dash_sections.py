"""Render-секции дашборда (KPI, графики, когорты, регионы, дубли, источники).

Каждая функция самодостаточна: считает нужное из deals_df и рисует через st.
"""

from datetime import datetime

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from dash_core import (
    TYPE_COLORS, COLOR_SUCCESS, COLOR_NDZ, COLOR_NEW, classify_status,
    extract_phone, SLA_WINDOWS_HOURS,
)


def render_kpi(deals_df):
    total = len(deals_df)
    success = len(deals_df[deals_df["Тип"] == "Успешный"])
    in_progress = len(deals_df[deals_df["Тип"] == "В процессе"])
    ndz = len(deals_df[deals_df["Тип"] == "НДЗ"])
    trash = len(deals_df[deals_df["Тип"].isin(["Брак", "Резерв"])])
    new_leads = len(deals_df[deals_df["Тип"] == "Новый"])
    processed = total - new_leads
    results = success + in_progress  # результативные: деньги + в работе
    conv_total = round(results / total * 100, 1) if total else 0
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


def render_type_status_charts(deals_df):
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


def render_source_loss(deals_df):
    total = len(deals_df)
    success = len(deals_df[deals_df["Тип"] == "Успешный"])
    ndz = len(deals_df[deals_df["Тип"] == "НДЗ"])
    trash = len(deals_df[deals_df["Тип"].isin(["Брак", "Резерв"])])
    new_leads = len(deals_df[deals_df["Тип"] == "Новый"])
    processed = total - new_leads

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


def render_daily(trainity_df, date_from):
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


def render_cohort(trainity_df, deals_df):
    st.subheader("📅 Когортный анализ")
    st.caption("Лиды, загруженные в определённый день — какой % обработан через 1/3/7 дней?")

    if not (len(trainity_df) > 0 and "_dt" in trainity_df.columns and len(deals_df) > 0):
        return

    phone_load_date = {}
    for _, row in trainity_df.iterrows():
        p = extract_phone(row.get("Телефон", ""))
        dt = row.get("_dt")
        if p and pd.notna(dt):
            phone_load_date[p] = dt

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

    if not cohort_rows:
        return

    cohort_df = pd.DataFrame(cohort_rows)
    cohort_stats = []
    for load_date, group in cohort_df.groupby("Дата загрузки"):
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

    col_coh1, col_coh2 = st.columns(2)
    with col_coh1:
        fig_coh = go.Figure()
        fig_coh.add_trace(go.Bar(
            x=cohort_result["Дата загрузки"].astype(str),
            y=cohort_result["% обработано"],
            name="% обработано", marker_color=COLOR_NDZ,
            text=cohort_result["% обработано"].apply(lambda x: f"{x}%"),
            textposition="outside", textfont=dict(color="#e2e8f0"),
        ))
        fig_coh.add_trace(go.Bar(
            x=cohort_result["Дата загрузки"].astype(str),
            y=cohort_result["% конверсия"],
            name="% конверсия", marker_color=COLOR_SUCCESS,
            text=cohort_result["% конверсия"].apply(lambda x: f"{x}%"),
            textposition="outside", textfont=dict(color="#e2e8f0"),
        ))
        fig_coh.update_layout(
            barmode="group", height=400, margin=dict(l=10, r=10, t=30, b=10),
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


def render_regions(deals_df):
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
                rg_stats = phones_for_regions.groupby("Регион").agg(
                    Всего=("Тип", "count"),
                    Успешных=("Тип", lambda x: (x == "Успешный").sum()),
                ).reset_index()
                rg_stats["Конверсия %"] = (rg_stats["Успешных"] / rg_stats["Всего"] * 100).round(1)
                rg_stats = rg_stats.sort_values("Всего", ascending=False).head(15)
                st.dataframe(rg_stats, width="stretch", hide_index=True, height=380)
    except ImportError:
        st.info("Модуль phone_info не найден — регионы недоступны на Streamlit Cloud")


def render_dups(deals_raw_df):
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


def render_source_table(deals_df):
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
