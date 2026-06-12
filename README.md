# Startup Idea Validator

AI-powered chatbot that researches and validates startup ideas using real-time market data.

## What It Does

1. **Gathers context** — Asks targeted questions about your product, target customer, problem, positioning, and go-to-market strategy.
2. **Researches the market** — Runs parallel searches via Serper (news), Google Trends signals, and Wikipedia to collect evidence.
3. **Delivers a verdict** — Produces a structured validation report covering market sizing, competitive landscape, risks, and a final viability rating.
4. **Stays conversational** — After research, you can ask follow-up questions, request deeper dives (tools are re-invoked automatically), or provide financial numbers for unit economics analysis.

## Tech Stack

| Component | Technology |
|-----------|-----------|
| LLM | Google Gemini 2.5 Flash (via LangChain) |
| Frontend | Streamlit |
| Search | Serper API (news + web) |
| Background Info | Wikipedia REST API |
| Persistence | SQLite |
| Tool Calling | LangChain `bind_tools` |

## Setup

```bash
# Clone and enter the directory

# Create virtual environment
python -m venv .venv
.venv\Scripts\activate   # Windows
# source .venv/bin/activate  # macOS/Linux

# Install dependencies
pip install -r requirements.txt

# Configure API keys
copy .env.example .env
# Edit .env and add your keys:
#   GOOGLE_API_KEY   — Google AI Studio
#   SERPER_API_KEY   — serper.dev
```

## Run

```bash
streamlit run app.py
```

## Project Structure

```
app.py        — Streamlit frontend (chat UI, tool badges, data expanders)
backend.py    — Core logic (state machine, LLM calls, research orchestration)
tools.py      — Search tools (news, trends, wikipedia, financial calculator)
prompt.py     — System prompt for the LLM
```

## Features

- Tool usage visible in the UI with status badges
- Expandable sections to inspect raw news, trends, and wiki data
- Financial viability calculator (LTV, LTV/CAC, payback) triggered when you provide numbers
- Deep-dive searches auto-triggered by follow-up questions
- SQLite persistence for session history
