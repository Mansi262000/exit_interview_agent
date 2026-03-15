# Exit Interview Agent — Execution Guide

> Agentic AI system that conducts empathetic exit interviews, scores sentiment in real-time, detects HR red flags, and generates structured insight reports.

---

## Prerequisites

| Requirement | Version |
|---|---|
| Python | 3.10+ |
| Anthropic API key | Free tier works |

---

## Setup (5 minutes)

### 1 — Clone / navigate to the project folder

```bash
cd gen-ai/gen-ai-mansi/agent_project_mansi
```

### 2 — Create a virtual environment

```bash
python -m venv .venv
source .venv/bin/activate        # Mac/Linux
.venv\Scripts\activate           # Windows
```

### 3 — Install dependencies

```bash
pip install -r requirements.txt
```

### 4 — Set your Anthropic API key

**Option A — `.env` file (recommended)**
```bash
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env
```

**Option B — environment variable**
```bash
export ANTHROPIC_API_KEY=sk-ant-...   # Mac/Linux
set ANTHROPIC_API_KEY=sk-ant-...      # Windows
```

Get a free API key at: https://console.anthropic.com

---

## Running the Agent

### Live Interview Mode

The agent conducts a real-time conversation with you via terminal prompts.

```bash
python exit_interview_agent.py
```

Then choose **L** when prompted for mode.

**Exit early at any time** by typing: `exit`, `quit`, or `end`

---

### Demo / Mock Mode (no user input needed)

Pre-built realistic answers run automatically — great for demos and testing.

```bash
python exit_interview_agent.py
```

Choose **D** when prompted for mode.

---

### Run the Jupyter Notebook

```bash
jupyter notebook exit_interview_agent.ipynb
```

Run all cells top-to-bottom. Cell 12 contains the live interview runner; Cell 13 runs in demo mode.

---

## Outputs

Every interview session produces two files in `outputs/`:

| File | Format | Purpose |
|---|---|---|
| `interview_<id>.json` | JSON | Machine-readable structured data, suitable for HRIS / database ingestion |
| `interview_<id>_report.md` | Markdown | Human-readable report for HR teams |

The session `<id>` is the first 8 characters of the UUID assigned to that session.

---

## Customising Questions

Edit `config.yaml` — no code changes needed.

```yaml
questions:
  - id: "Q7"
    category: "compensation"
    text: "Did compensation and benefits meet your expectations?"
    required: false
```

HR teams own this file. Engineers own the code. Separation of concerns.

---

## Project Structure

```
agent_project_mansi/
├── exit_interview_agent.py        ← Main agent (run this)
├── exit_interview_agent.ipynb     ← Jupyter notebook version
├── config.yaml                    ← Question bank + model settings
├── requirements.txt               ← Python dependencies
├── README.md                      ← This file
├── DOCUMENTATION.md               ← Architecture + design decisions
├── QA_GUIDE.md                    ← Feature-by-feature Q&A
└── outputs/
    └── sample_output.json         ← Example completed interview output
```

---

## Troubleshooting

| Error | Fix |
|---|---|
| `AuthenticationError` | Check `ANTHROPIC_API_KEY` is set correctly |
| `ModuleNotFoundError` | Run `pip install -r requirements.txt` in your venv |
| `RateLimitError` | The agent auto-retries with backoff — wait a moment |
| Config not loading | Ensure `config.yaml` is in the same folder as the script |
