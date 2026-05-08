from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st


def metric_card(label: str, value: Any) -> None:
    st.metric(label, value if value not in ("", None) else "N/A")


def show_dataframe_or_empty(df: pd.DataFrame, empty_text: str = "无") -> None:
    if df.empty:
        st.caption(empty_text)
    else:
        st.dataframe(df, use_container_width=True, hide_index=True)


def status_badge(status: str) -> None:
    if status == "recommended_for_observation":
        st.success("recommended_for_observation")
    elif status == "research_only":
        st.warning("research_only，不建议作为主跟随策略。")
    elif status == "defensive_only":
        st.info("defensive_only，不作为主策略。")
    else:
        st.error(status or "unknown")
