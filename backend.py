import sqlite3
import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Callable
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed

from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, Field

from prompt import SYSTEM_PROMPT
from tools import news_search, google_trends_tool, wikipedia_lookup, financial_viability_check

load_dotenv()
api_key = os.getenv("GOOGLE_API_KEY")

llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0.7, google_api_key=api_key)

# LLM variant with all tools bound for post-research deep-dive
all_tools = [news_search, google_trends_tool, wikipedia_lookup, financial_viability_check]
llm_with_tools = llm.bind_tools(all_tools)

# Map tool names to their functions for execution
TOOL_MAP = {
    "news_search": news_search,
    "google_trends": google_trends_tool,
    "wikipedia_lookup": wikipedia_lookup,
    "financial_viability_check": financial_viability_check,
}

# ==============================================================================
# DATABASE
# ==============================================================================

DB_PATH = "startup_validation.db"


def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "CREATE TABLE IF NOT EXISTS sessions "
        "(session_id TEXT PRIMARY KEY, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )
    c.execute(
        "CREATE TABLE IF NOT EXISTS messages "
        "(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, content TEXT, "
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )
    c.execute(
        "CREATE TABLE IF NOT EXISTS params "
        "(session_id TEXT PRIMARY KEY, data TEXT)"
    )
    conn.commit()
    conn.close()


def _db_save_message(sid: str, role: str, content: str):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT INTO messages (session_id, role, content) VALUES (?,?,?)", (sid, role, content))
    conn.commit()
    conn.close()


def _db_save_params(sid: str, params: dict):
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("INSERT INTO params (session_id, data) VALUES (?,?)", (sid, json.dumps(params)))
    except sqlite3.IntegrityError:
        conn.execute("UPDATE params SET data=? WHERE session_id=?", (json.dumps(params), sid))
    conn.commit()
    conn.close()


# ==============================================================================
# STATE
# ==============================================================================


class ChatState(BaseModel):
    session_id: str
    messages: List[Dict[str, str]] = Field(default_factory=list)  # {role, content}
    business_params: Dict[str, Any] = Field(default_factory=dict)
    research_data: Dict[str, Any] = Field(default_factory=dict)  # raw evidence kept for user
    phase: str = "gathering"  # gathering | researching | conversing
    research_done: bool = False

    class Config:
        arbitrary_types_allowed = True


# ==============================================================================
# HELPERS
# ==============================================================================

REQUIRED_FIELDS = [
    "product_name",
    "product_category",
    "target_customer",
    "target_location",
    "problem_statement",
    "positioning",
    "promotion_channels",
]


def _build_llm_messages(state: ChatState, extra_system: str = "") -> list[dict]:
    """Build the message list for the LLM call."""
    msgs = [{"role": "user", "content": SYSTEM_PROMPT + "\n" + extra_system}]
    for m in state.messages:
        msgs.append({"role": m["role"], "content": m["content"]})
    return msgs


def _ensure_str(content) -> str:
    """Gemini sometimes returns content as a list of parts. Coerce to str."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, str):
                parts.append(p)
            elif hasattr(p, "text"):
                parts.append(p.text)
            elif isinstance(p, dict) and "text" in p:
                parts.append(p["text"])
            else:
                parts.append(str(p))
        return "\n".join(parts)
    return str(content)


def _llm_call(messages: list[dict]) -> str:
    """Simple LLM invoke, returns text content."""
    # Convert to langchain format
    from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

    lc_msgs = []
    for m in messages:
        if m["role"] == "user" or m["role"] == "human":
            lc_msgs.append(HumanMessage(content=m["content"]))
        elif m["role"] == "assistant":
            lc_msgs.append(AIMessage(content=m["content"]))
        else:
            lc_msgs.append(SystemMessage(content=m["content"]))
    resp = llm.invoke(lc_msgs)
    return _ensure_str(resp.content)


def _to_lc_messages(messages: list[dict]):
    """Convert dict messages to LangChain message objects."""
    from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

    lc_msgs = []
    for m in messages:
        if m["role"] in ("user", "human"):
            lc_msgs.append(HumanMessage(content=m["content"]))
        elif m["role"] == "assistant":
            lc_msgs.append(AIMessage(content=m["content"]))
        else:
            lc_msgs.append(SystemMessage(content=m["content"]))
    return lc_msgs


def _llm_call_with_tools(state: ChatState):
    """Call LLM with all tools bound for post-research conversation.

    Returns (reply_text, list_of_tool_events).
    The LLM can optionally call search tools for deeper research or
    financial_viability_check when the user provides numbers.
    """
    extra = (
        "\n\nCURRENT PHASE: Post-research conversation.\n"
        "Research has been completed. You can answer follow-up questions, "
        "provide deeper analysis, or help refine the strategy.\n\n"
        "You have access to these tools and SHOULD use them when the user "
        "asks to dig deeper, explore a specific angle, or wants fresh data:\n"
        "- news_search: Search for recent news on a specific topic or competitor\n"
        "- google_trends: Check search trends for a keyword\n"
        "- wikipedia_lookup: Get background on a company, technology, or concept\n"
        "- financial_viability_check: Calculate LTV/CAC/payback (ONLY when user provides real numbers)\n\n"
        "Call a tool whenever the user asks about competitors, market segments, "
        "specific companies, recent developments, or any topic that benefits from "
        "live data. Do NOT make up data you could look up."
    )
    msgs = _build_llm_messages(state, extra)
    lc_msgs = _to_lc_messages(msgs)

    resp = llm_with_tools.invoke(lc_msgs)

    tool_events = []

    # Check if LLM wants to call any tools
    if resp.tool_calls:
        tool_results_text = []

        for tc in resp.tool_calls:
            tool_name = tc["name"]
            tool_fn = TOOL_MAP.get(tool_name)
            if not tool_fn:
                continue

            try:
                result = tool_fn.func(**tc["args"])
            except Exception as e:
                result = {"error": str(e)}

            tool_events.append({"event": "tool_call", "tool": tool_name, "data": result})
            tool_results_text.append(
                f"Tool {tool_name} returned:\n{json.dumps(result, indent=2)[:1500]}"
            )

            # Update research_data with fresh results for expandable views
            if tool_name in ("news_search", "google_trends", "wikipedia_lookup"):
                state.research_data[tool_name] = result

        # Feed all tool results back to LLM for a natural-language summary
        from langchain_core.messages import HumanMessage

        combined = "\n\n".join(tool_results_text)
        lc_msgs.append(resp)  # AI message with tool_calls
        lc_msgs.append(HumanMessage(
            content=(
                f"{combined}\n\n"
                "Using the tool results above, provide a helpful and detailed answer "
                "to the user's question. Cite specific findings from the data."
            )
        ))
        final_resp = llm.invoke(lc_msgs)
        return _ensure_str(final_resp.content), tool_events
    else:
        return _ensure_str(resp.content), tool_events


def extract_params(state: ChatState) -> Dict[str, str]:
    """Ask LLM to extract structured params from conversation so far."""
    convo = ""
    for m in state.messages[-12:]:
        tag = "User" if m["role"] in ("human", "user") else "Assistant"
        convo += f"\n{tag}: {m['content'][:600]}"

    prompt = (
        "From the conversation below extract ONLY fields that are clearly mentioned.\n"
        "Fields: product_name, product_category, target_customer, target_location, "
        "product_price, positioning, promotion_channels, problem_statement, customer_feedback, assumptions, competitors.\n"
        "Return ONLY a JSON object. If a field is not mentioned leave it out.\n"
        f"\nCONVERSATION:{convo}"
    )
    try:
        raw = _llm_call([{"role": "user", "content": prompt}])
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(raw[start:end])
    except Exception:
        pass
    return {}


# ==============================================================================
# RESEARCH — runs 3 API calls in parallel, reports progress via callback
# ==============================================================================

def run_research(
    params: Dict[str, Any],
    on_tool_start: Optional[Callable[[str], None]] = None,
    on_tool_done: Optional[Callable[[str, Any], None]] = None,
) -> Dict[str, Any]:
    """Execute the 3 evidence tools in parallel. Returns raw data dict."""
    category = params.get("product_category") or params.get("product_name") or "startup"
    location = params.get("target_location", "India")

    product = params.get("product_name", category)
    problem = params.get("problem_statement", "")

    # Build targeted search queries
    news_query = f"{product} {category} startup market {location} 2025 2026"
    if problem:
        news_query = f"{category} {problem[:60]} market {location}"

    jobs = {
        "news_search": {
            "fn": news_search.func,
            "kwargs": {"query": news_query, "limit": 8},
        },
        "google_trends": {
            "fn": google_trends_tool.func,
            "kwargs": {"query": product if product != category else category},
        },
        "wikipedia_lookup": {
            "fn": wikipedia_lookup.func,
            "kwargs": {"topic": category, "max_chars": 2000},
        },
    }

    results: Dict[str, Any] = {}

    def _run(name: str, fn, kwargs):
        if on_tool_start:
            on_tool_start(name)
        try:
            data = fn(**kwargs)
        except Exception as e:
            data = {"error": str(e)}
        if on_tool_done:
            on_tool_done(name, data)
        return name, data

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(_run, n, j["fn"], j["kwargs"]) for n, j in jobs.items()]
        for f in as_completed(futures):
            name, data = f.result()
            results[name] = data

    return results


# ==============================================================================
# PUBLIC API
# ==============================================================================


def initialize_chatbot() -> ChatState:
    init_db()
    sid = f"s_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT INTO sessions (session_id) VALUES (?)", (sid,))
    conn.commit()
    conn.close()

    state = ChatState(session_id=sid)
    greeting = (
        "Hello! 👋 I'm your **Startup Idea Validator**.\n\n"
        "Tell me about your startup idea — what it is, what problem it solves, "
        "and who your target customer is. I'll ask follow-up questions before "
        "doing any research.\n\n"
        "Go ahead! 🚀"
    )
    state.messages.append({"role": "assistant", "content": greeting})
    _db_save_message(sid, "assistant", greeting)
    return state


def get_llm_reply(state: ChatState) -> str:
    """Let the LLM decide what to say next based on full conversation context."""
    extra = ""
    if state.phase == "gathering":
        filled = [k for k in REQUIRED_FIELDS if state.business_params.get(k)]
        missing = [k for k in REQUIRED_FIELDS if k not in state.business_params]
        extra = (
            f"\n\nCURRENT PHASE: Gathering information.\n"
            f"Fields collected so far: {filled}\n"
            f"Fields still missing: {missing}\n"
            "Ask the user about the missing fields through natural conversation. "
            "Ask 2-3 questions at a time, not all at once. "
            "Do NOT proceed to research until you have most fields. "
            "Do NOT use any tools yet."
        )
    elif state.phase == "conversing" and state.research_done:
        # NOTE: This branch is only reached if get_llm_reply is called
        # during the analysis step (phase transitions mid-call).
        # The main conversing path uses _llm_call_with_tools instead.
        extra = (
            "\n\nCURRENT PHASE: Post-research conversation.\n"
            "Research has been completed. You can answer follow-up questions, "
            "provide deeper analysis, or help refine the strategy. "
            "Stay helpful and conversational."
        )
    msgs = _build_llm_messages(state, extra)
    return _llm_call(msgs)


def should_start_research(state: ChatState) -> bool:
    """Check if we have enough info to start research."""
    filled = sum(1 for k in REQUIRED_FIELDS if state.business_params.get(k))
    return filled >= 5  # at least 5 of 7 fields


def chat(state: ChatState, user_message: str):
    """Process user message. Returns (state, reply_text, tool_events).

    tool_events is a list of {"event": "start"|"done", "tool": name, "data": ...}
    populated only during research phase.
    """
    # Record user message
    state.messages.append({"role": "human", "content": user_message})
    _db_save_message(state.session_id, "human", user_message)

    tool_events: List[Dict[str, Any]] = []

    if state.phase == "gathering":
        # Extract params from conversation
        extracted = extract_params(state)
        state.business_params.update({k: v for k, v in extracted.items() if v})
        _db_save_params(state.session_id, state.business_params)

        # Check if ready
        if should_start_research(state):
            # Confirm with summary then research
            summary_lines = []
            for k, v in state.business_params.items():
                summary_lines.append(f"• **{k.replace('_', ' ').title()}**: {v}")
            summary = "\n".join(summary_lines)

            confirm_msg = (
                f"Great, here's what I have so far:\n\n{summary}\n\n"
                "Let me now research the market for you... 🔍"
            )
            state.messages.append({"role": "assistant", "content": confirm_msg})
            _db_save_message(state.session_id, "assistant", confirm_msg)
            tool_events.append({"event": "confirm", "content": confirm_msg})

            # Run research
            state.phase = "researching"

            def on_start(name):
                tool_events.append({"event": "start", "tool": name})

            def on_done(name, data):
                tool_events.append({"event": "done", "tool": name, "data": data})

            state.research_data = run_research(state.business_params, on_start, on_done)

            # Build analysis prompt with evidence
            evidence_text = json.dumps(state.research_data, indent=2)[:3000]
            params_text = json.dumps(state.business_params, indent=2)

            analysis_instruction = (
                f"\n\nRESEARCH RESULTS (from news, trends, wikipedia):\n{evidence_text}\n\n"
                f"BUSINESS PARAMETERS:\n{params_text}\n\n"
                "Now provide a comprehensive startup validation analysis covering:\n"
                "1. Problem & Opportunity\n"
                "2. Market Sizing (TAM/SAM/SOM)\n"
                "3. Competitive Landscape\n"
                "4. Value Proposition\n"
                "5. Go-to-Market Strategy\n"
                "6. Key Risks & Assumptions\n"
                "7. Self-Critique: what could be wrong with this analysis?\n"
                "8. Final Verdict: VIABLE & DEFENSIBLE / VIABLE BUT RISKY / NEEDS VALIDATION / NOT RECOMMENDED\n"
                "9. Top 3 immediate action items\n\n"
                "Cite evidence from the research where possible."
            )
            state.messages.append({"role": "human", "content": analysis_instruction})

            # Get analysis from LLM
            analysis = get_llm_reply(state)
            # Remove the injected instruction from visible history
            state.messages.pop()

            state.messages.append({"role": "assistant", "content": analysis})
            _db_save_message(state.session_id, "assistant", analysis)

            state.phase = "conversing"
            state.research_done = True
            tool_events.append({"event": "analysis", "content": analysis})

            return state, tool_events

        else:
            # Still gathering — let LLM ask more questions
            reply = get_llm_reply(state)
            state.messages.append({"role": "assistant", "content": reply})
            _db_save_message(state.session_id, "assistant", reply)
            tool_events.append({"event": "reply", "content": reply})
            return state, tool_events

    else:
        # Post-research conversation — user can ask anything
        # Check if user wants to see raw data
        lower = user_message.lower()
        if any(kw in lower for kw in ["show news", "show trends", "show wiki", "raw data", "show research", "show evidence"]):
            tool_events.append({"event": "show_data", "data": state.research_data})

        # Use tool-calling LLM — can invoke search tools or financial tool
        reply, call_events = _llm_call_with_tools(state)
        tool_events.extend(call_events)

        state.messages.append({"role": "assistant", "content": reply})
        _db_save_message(state.session_id, "assistant", reply)
        tool_events.append({"event": "reply", "content": reply})
        return state, tool_events
