import streamlit as st
import json
from backend import initialize_chatbot, chat, ChatState

# ==============================================================================
# PAGE CONFIG
# ==============================================================================

st.set_page_config(
    page_title="Startup Validator",
    page_icon="🚀",
    layout="centered",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
    .main { max-width: 900px; margin: 0 auto; }
    .tool-badge {
        display: inline-block;
        padding: 2px 10px;
        border-radius: 12px;
        font-size: 0.82em;
        margin: 2px 4px;
    }
    .tool-running { background: #fff3cd; color: #856404; }
    .tool-done    { background: #d4edda; color: #155724; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ==============================================================================
# SESSION
# ==============================================================================


def init_session():
    if "state" not in st.session_state:
        st.session_state.state = initialize_chatbot()


# ==============================================================================
# DISPLAY HELPERS
# ==============================================================================

TOOL_LABELS = {
    "news_search": "📰 News Search",
    "google_trends": "📈 Google Trends",
    "wikipedia_lookup": "📖 Wikipedia",
}


def render_tool_events(events):
    """Show tool usage status badges and raw-data expanders."""
    started = set()
    done = {}

    for ev in events:
        if ev["event"] == "start":
            started.add(ev["tool"])
        elif ev["event"] == "done":
            done[ev["tool"]] = ev.get("data")

    if not started and not done:
        return

    st.markdown("**🔧 Tools used:**")
    cols = st.columns(len(TOOL_LABELS))
    for i, (key, label) in enumerate(TOOL_LABELS.items()):
        with cols[i]:
            if key in done:
                st.markdown(
                    f'<span class="tool-badge tool-done">✅ {label}</span>',
                    unsafe_allow_html=True,
                )
            elif key in started:
                st.markdown(
                    f'<span class="tool-badge tool-running">⏳ {label}</span>',
                    unsafe_allow_html=True,
                )


def render_research_expanders(research_data):
    """Expandable sections so user can inspect raw evidence on demand."""
    if not research_data:
        return

    with st.expander("📰 News Articles Fetched", expanded=False):
        news = research_data.get("news_search", {})
        items = news.get("items", [])
        if items:
            for item in items:
                title = item.get("title", "Untitled")
                link = item.get("link", "")
                snippet = item.get("snippet", "")
                source = item.get("source", "")
                date = item.get("date", "")
                st.markdown(f"**[{title}]({link})**")
                if snippet:
                    st.caption(f"{snippet}")
                if source or date:
                    st.caption(f"_{source}_ · {date}")
                st.markdown("---")
        else:
            st.info("No news results found.")

    with st.expander("📈 Google Trends Data", expanded=False):
        trends = research_data.get("google_trends", {})
        raw = trends.get("raw", "")
        if raw:
            st.text(raw)
        else:
            st.info("No trends data found.")

    with st.expander("📖 Wikipedia Summary", expanded=False):
        wiki = research_data.get("wikipedia_lookup", {})
        summary = wiki.get("summary", "")
        if summary:
            st.write(summary)
        else:
            st.info("No Wikipedia data found.")


def render_financial_results(data):
    """Show financial_viability_check output in a structured expander."""
    if not data or "error" in data:
        st.warning(f"Financial calculation failed: {data.get('error', 'unknown')}")
        return

    with st.expander("💰 Financial Viability Check", expanded=True):
        inputs = data.get("inputs", {})
        outputs = data.get("outputs", {})
        health = data.get("health_checks", {})

        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**Inputs**")
            st.write(f"- ARPU (monthly): **${inputs.get('arpu_monthly', 'N/A')}**")
            st.write(f"- Gross Margin: **{inputs.get('gross_margin', 'N/A')}**")
            st.write(f"- Monthly Churn: **{inputs.get('churn_monthly', 'N/A')}**")
            st.write(f"- CAC: **${inputs.get('cac', 'N/A')}**")
        with col2:
            st.markdown("**Results**")
            st.write(f"- Contribution Margin: **${outputs.get('contribution_margin_monthly', 'N/A')}/mo**")
            st.write(f"- LTV: **${outputs.get('ltv', 'N/A')}**")
            ltv_cac = outputs.get("ltv_cac_ratio", 0)
            st.write(f"- LTV/CAC: **{ltv_cac}x** {'✅' if health.get('ltv_cac_good') else '⚠️'}")
            payback = outputs.get("payback_months", 0)
            st.write(f"- Payback: **{payback} months** {'✅' if health.get('payback_good') else '⚠️'}")


DEEP_DIVE_LABELS = {
    "news_search": "📰 News Search",
    "google_trends": "📈 Google Trends",
    "wikipedia_lookup": "📖 Wikipedia",
    "financial_viability_check": "💰 Financial Check",
}


def render_deep_dive_tools(tool_call_events):
    """Show tool call badges and expandable results for deep-dive searches."""
    tool_names = [e["tool"] for e in tool_call_events]
    badges = " ".join(
        f'<span class="tool-badge tool-done">🔍 {DEEP_DIVE_LABELS.get(n, n)}</span>'
        for n in tool_names
    )
    st.markdown(f"**🔧 Deep-dive tools used:** {badges}", unsafe_allow_html=True)

    for ev in tool_call_events:
        name = ev["tool"]
        data = ev.get("data", {})

        if name == "financial_viability_check":
            render_financial_results(data)
        elif name == "news_search":
            with st.expander(f"📰 Fresh News Results", expanded=False):
                items = data.get("items", [])
                if items:
                    for item in items:
                        title = item.get("title", "Untitled")
                        link = item.get("link", "")
                        snippet = item.get("snippet", "")
                        st.markdown(f"**[{title}]({link})**")
                        if snippet:
                            st.caption(snippet)
                        st.markdown("---")
                else:
                    st.info("No news results returned.")
        elif name == "google_trends":
            with st.expander(f"📈 Fresh Trends Data", expanded=False):
                raw = data.get("raw", "")
                if raw:
                    st.text(raw)
                else:
                    st.info("No trends data returned.")
        elif name == "wikipedia_lookup":
            with st.expander(f"📖 Wikipedia Result", expanded=False):
                summary = data.get("summary", "")
                if summary:
                    st.write(summary)
                else:
                    st.info("No Wikipedia data returned.")


# ==============================================================================
# MAIN
# ==============================================================================

def main():
    init_session()

    st.title("🚀 Startup Idea Validator")
    st.caption("Validate your startup idea with through research")
    st.markdown("---")

    state: ChatState = st.session_state.state

    # ── Render conversation history ──────────────────────────────────────
    for msg in state.messages:
        role = msg["role"]
        if role in ("human", "user"):
            with st.chat_message("user", avatar="👤"):
                st.write(msg["content"])
        elif role == "assistant":
            with st.chat_message("assistant", avatar="🤖"):
                st.write(msg["content"])

    # If research has been done, show raw-data expanders once
    if state.research_done and state.research_data:
        render_research_expanders(state.research_data)

    # ── Chat input (always available) ────────────────────────────────────
    user_input = st.chat_input("Type your message...")

    if user_input:
        # Show user message immediately
        with st.chat_message("user", avatar="👤"):
            st.write(user_input)

        # Process
        with st.chat_message("assistant", avatar="🤖"):
            with st.spinner("Thinking..."):
                state, tool_events = chat(state, user_input)
                st.session_state.state = state

            # Show tool progress if research happened this turn
            tool_starts = [e for e in tool_events if e["event"] in ("start", "done")]
            if tool_starts:
                render_tool_events(tool_events)

            # Show deep-dive tool calls (search re-invocations)
            deep_dive_calls = [e for e in tool_events if e["event"] == "tool_call"]
            if deep_dive_calls:
                render_deep_dive_tools(deep_dive_calls)

            # Show the reply
            for ev in tool_events:
                if ev["event"] == "confirm":
                    st.info(ev["content"])
                elif ev["event"] == "analysis":
                    st.write(ev["content"])
                elif ev["event"] == "reply":
                    st.write(ev["content"])
                elif ev["event"] == "show_data" and ev.get("data"):
                    render_research_expanders(ev["data"])

        st.rerun()


if __name__ == "__main__":
    main()
