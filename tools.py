from __future__ import annotations

from langchain_core.tools import tool
import os
import requests
from dotenv import load_dotenv
from typing import List, Dict, Any, Optional
import re
import warnings

load_dotenv()

# Getting the API Keys
serper_api_key = os.getenv("SERPER_API_KEY")

# Suppress SSL warnings for direct requests
warnings.filterwarnings("ignore", message="Unverified HTTPS request")


# ==========================================================
# Direct API fallbacks for SSL-problematic environments
# ==========================================================

def _serper_direct(query: str, num: int = 10, search_type: str = "news") -> Dict[str, Any]:
    """Direct Serper API call with SSL verification disabled as fallback."""
    endpoint = f"https://google.serper.dev/{search_type}"
    resp = requests.post(
        endpoint,
        json={"q": query, "num": num},
        headers={"X-API-KEY": serper_api_key},
        timeout=20,
        verify=False,
    )
    resp.raise_for_status()
    return resp.json()


def _wikipedia_direct(topic: str, max_chars: int = 2000) -> str:
    """Direct Wikipedia API call with SSL verification disabled as fallback."""
    try:
        resp = requests.get(
            "https://en.wikipedia.org/api/rest_v1/page/summary/" + requests.utils.quote(topic),
            headers={"User-Agent": "StartupValidator/1.0"},
            timeout=15,
            verify=False,
        )
        if resp.ok:
            data = resp.json()
            return data.get("extract", "")[:max_chars]
    except Exception:
        pass
    # Second fallback: use Serper web search for wiki-like background
    try:
        data = _serper_direct(f"{topic} overview background", num=5, search_type="search")
        snippets = []
        for item in (data.get("organic") or [])[:5]:
            snippet = item.get("snippet", "")
            if snippet:
                snippets.append(snippet)
        return "\n\n".join(snippets)[:max_chars] if snippets else ""
    except Exception:
        return ""


def _trends_via_serper(query: str) -> str:
    """Fallback: use Serper web search to get trend information."""
    try:
        data = _serper_direct(f"{query} market trends growth 2025 2026", num=5, search_type="search")
        snippets = []
        for item in (data.get("organic") or [])[:5]:
            title = item.get("title", "")
            snippet = item.get("snippet", "")
            if snippet:
                snippets.append(f"• {title}: {snippet}")
        return "\n".join(snippets) if snippets else ""
    except Exception:
        return ""


# ==========================================================
# Internal helpers
# ==========================================================

def _safe_str(x: Any) -> str:
    if x is None:
        return ""
    return str(x)


def _extract_domains(urls: List[str]) -> List[str]:
    domains = []
    for u in urls:
        m = re.search(r"https?://([^/]+)/?", u)
        if m:
            domains.append(m.group(1).lower())
    return sorted(set(domains))


def _compact_news_results(raw: Any, limit: int = 10) -> Dict[str, Any]:
    if isinstance(raw, str):
        return {"count": 0, "domains": [], "items": [], "raw_text": raw}
    data = raw if isinstance(raw, dict) else {"raw": raw}
    # Serper can return results under different keys
    items = (
        data.get("news")
        or data.get("organic")
        or data.get("results")
        or data.get("articles")
        or []
    )
    compact = []
    for it in items[:limit]:
        if not isinstance(it, dict):
            continue
        compact.append({
            "title": it.get("title", "Untitled"),
            "link": it.get("link", ""),
            "snippet": it.get("snippet") or it.get("description") or it.get("body", ""),
            "source": it.get("source", ""),
            "date": it.get("date") or it.get("publishedDate", ""),
        })
    urls = [x.get("link") for x in compact if x.get("link")]
    return {
        "count": len(compact),
        "domains": _extract_domains(urls),
        "items": compact,
    }


# ==========================================================
# Evidence tools — the only ones that call external APIs
# ==========================================================

@tool("news_search")
def news_search(query: str, limit: int = 10) -> Dict[str, Any]:
    """Search recent NEWS articles via Serper.

    Args:
        query: search string (e.g. "AI recruiting India market size 2026")
        limit: max items to return

    Returns:
        Compact list of news results with title, link, snippet, source, date.
    """
    try:
        raw = _serper_direct(query, num=limit, search_type="news")
    except Exception as e:
        return {"count": 0, "domains": [], "items": [], "error": str(e)}
    return _compact_news_results(raw, limit=limit)


@tool("google_trends")
def google_trends_tool(query: str) -> Dict[str, Any]:
    """Fetch market trend signals for a keyword.

    Args:
        query: trends keyword (e.g. "resume parser")

    Returns:
        Trend-related snippets from web search.
    """
    text = _trends_via_serper(query)
    if text:
        return {"raw": text}
    return {"raw": "", "error": "Could not fetch trend data"}


@tool("wikipedia_lookup")
def wikipedia_lookup(topic: str, max_chars: int = 2000) -> Dict[str, Any]:
    """Look up a topic on Wikipedia for neutral background.

    Args:
        topic: entity name (e.g. "LinkedIn", "Recruitment")
        max_chars: max summary length

    Returns:
        Summary text + truncation flag.
    """
    text = _wikipedia_direct(topic, max_chars)
    if text:
        return {"summary": text, "truncated": len(text) >= max_chars}
    return {"summary": "", "truncated": False, "error": "Could not fetch Wikipedia data"}


# ==========================================================
# Pure-compute tool (no API calls, fast)
# ==========================================================

@tool("financial_viability_check")
def financial_viability_check(
    arpu_monthly: float,
    gross_margin: float,
    churn_monthly: float,
    cac: float,
    monthly_variable_cost: float = 0.0,
) -> Dict[str, Any]:
    """Calculate unit economics: LTV, LTV/CAC, payback period.

    Args:
        arpu_monthly: revenue per customer per month
        gross_margin: 0‥1
        churn_monthly: 0‥1
        cac: customer acquisition cost
        monthly_variable_cost: variable cost per customer per month

    Returns:
        Contribution margin, LTV, LTV/CAC ratio, payback months, health checks.
    """
    churn = max(churn_monthly, 1e-6)
    contribution = (arpu_monthly - monthly_variable_cost) * gross_margin
    ltv = contribution / churn
    ltv_cac = ltv / max(cac, 1e-6)
    payback = cac / max(contribution, 1e-6)

    return {
        "inputs": {
            "arpu_monthly": arpu_monthly,
            "gross_margin": gross_margin,
            "churn_monthly": churn_monthly,
            "cac": cac,
            "monthly_variable_cost": monthly_variable_cost,
        },
        "outputs": {
            "contribution_margin_monthly": round(contribution, 2),
            "ltv": round(ltv, 2),
            "ltv_cac_ratio": round(ltv_cac, 2),
            "payback_months": round(payback, 2),
        },
        "health_checks": {
            "ltv_cac_good": ltv_cac >= 3.0,
            "payback_good": payback <= 6.0,
        },
    }


# ==========================================================
# Export
# ==========================================================

TOOLS = [news_search, google_trends_tool, wikipedia_lookup, financial_viability_check]