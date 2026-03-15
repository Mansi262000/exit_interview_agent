# Exit Interview Agent — Architecture & Design Documentation

---

## 1. System Overview

This system is a **production-grade multi-agent AI pipeline** that automates exit interviews end-to-end:
conducting a natural conversation with a departing employee, scoring sentiment in real-time, flagging
HR-sensitive language, and generating structured insight reports for HR teams — all without human interviewer involvement.

```
┌──────────────────────────────────────────────────────────────────────┐
│                     InterviewOrchestrator                            │
│                    (Finite State Machine)                            │
│                                                                      │
│   INIT → WARMUP → QUESTIONING ↔ FOLLOW_UP → CLOSING → ANALYZING    │
│                                                    ↓         ↓       │
│                                                COMPLETE  ABORTED     │
└────────────┬───────────────────────────────────────┬────────────────┘
             │                                       │
    ┌────────▼──────────┐                  ┌────────▼──────────┐
    │  InterviewerAgent  │                  │   AnalystAgent    │
    │  (Claude Haiku)    │                  │  (Claude Sonnet)  │
    │                    │                  │                   │
    │  • Greet employee  │                  │  • Read full      │
    │  • Ask questions   │                  │    transcript     │
    │  • Generate        │                  │  • Extract themes │
    │    follow-ups      │                  │  • Write summary  │
    │  • Close warmly    │                  │  • Recommend      │
    └────────┬───────────┘                  │    actions        │
             │                              └────────┬──────────┘
    ┌────────▼──────────────────────────────────────▼──────────┐
    │                 ReportGenerator                           │
    │         JSON (machine) + Markdown (human)                 │
    └───────────────────────────────────────────────────────────┘
```

---

## 2. Component Breakdown

### 2.1 InterviewOrchestrator

The central controller. It owns the **state machine**, coordinates both agents, and writes outputs.

**States:**
| State | Description |
|---|---|
| `INIT` | System startup |
| `WARMUP` | Session created, greeting delivered |
| `QUESTIONING` | Core question being asked |
| `FOLLOW_UP` | Context-aware follow-up being generated and asked |
| `CLOSING` | Graceful interview close |
| `ANALYZING` | AnalystAgent generating insights |
| `COMPLETE` | All outputs saved |
| `ABORTED` | Interrupted — partial report still generated |

---

### 2.2 InterviewerAgent (Claude Haiku)

Responsible for all **conversational turns**:
- Greets the employee with warmth and sets psychological safety
- Frames each question contextually (references the prior answer)
- Generates one targeted follow-up per response (up to `max_follow_ups` per session)
- Closes the interview gracefully

**Why Haiku?** Low-latency, cost-efficient (~$0.25/MTok). Perfectly sufficient for natural conversation delivery.

---

### 2.3 AnalystAgent (Claude Sonnet)

Responsible for **insight extraction** after the full transcript is available:
- Reads all Q&A pairs plus the per-response sentiment trace
- Produces: executive summary, 3–5 HR recommendations, overall sentiment classification
- Uses a strict structured output format (parsed deterministically)

**Why Sonnet?** Deep reasoning, nuanced analysis. One call per session — the quality premium is worth it.

---

### 2.4 SentimentScorer

Lightweight **lexicon-based scorer** (no ML model, no API call):
- Runs on every answer immediately after it's captured
- Returns `SentimentLabel` (enum) + `float` score in `[-1.0, +1.0]`
- Feeds into the Analyst's context as a "sentiment trace"

**Why not a ML model?** Zero latency, no dependency, deterministic, sufficient resolution for interview responses. The LLM Analyst provides the authoritative qualitative sentiment anyway.

---

### 2.5 HR Red Flag Detector

Scans every answer for a configurable keyword list (harassment, discrimination, hostile, etc.).
- Sets `red_flag=True` on the `ResponseRecord`
- Sets `red_flags_detected=True` on the `InterviewSession`
- Alerts in the terminal immediately
- Includes an escalation recommendation in the Analyst's output
- Marks responses with 🚩 in the Markdown report

**Why inline, not post-hoc?** HR needs to know in real-time so they can take immediate action if needed.

---

### 2.6 Pydantic Data Models

Two core models: `ResponseRecord` and `InterviewSession`.

- All fields are typed and validated at assignment time
- `model_dump()` serializes directly to JSON — no manual serialization code
- Enum fields (`SentimentLabel`) are type-safe and self-documenting
- `Field(ge=-1.0, le=1.0)` on `sentiment_score` enforces data contract bounds

---

## 3. Data Flow

```
Employee types answer
       │
       ▼
SentimentScorer.score_sentiment(answer)
  → SentimentLabel + float score
       │
       ▼
RedFlagDetector._is_red_flag(answer)
  → bool, alert if True
       │
       ▼
InterviewerAgent.follow_up(question, answer)
  → contextual follow-up question (if budget allows)
       │
       ▼
ResponseRecord(Pydantic)  ← all fields validated
       │
       ▼ (after all questions)
AnalystAgent.analyze(session)
  → summary + recommendations + overall_sentiment
       │
       ▼
ReportGenerator
  → outputs/interview_<id>.json
  → outputs/interview_<id>_report.md
```

---

## 4. Professional Patterns Applied

| Pattern | Where | Why |
|---|---|---|
| Multi-agent with role separation | Interviewer vs Analyst | Different cognitive tasks → different models → better quality + cost |
| Finite State Machine | Orchestrator | Prevents impossible transitions; makes flow auditable |
| Pydantic data contracts | `ResponseRecord`, `InterviewSession` | Type safety, auto-serialisation, self-documenting |
| Retry + exponential backoff | `InterviewerAgent._call()` | Production resilience; transient API errors don't crash sessions |
| YAML-configurable question bank | `config.yaml` | HR owns questions, engineers own code — correct separation |
| UUID session IDs | `InterviewSession.session_id` | Enables correlation across HRIS, analytics, audit systems |
| ISO-8601 timestamps on each record | `ResponseRecord.timestamp` | Immutable audit trail; timezone-agnostic |
| Graceful partial handling | Orchestrator `try/except KeyboardInterrupt` | Incomplete sessions still produce valid reports — no data loss |
| Dual LLM tiers | Haiku (interview) + Sonnet (analysis) | Cost-optimised: expensive model only where quality matters |
| Demo/mock mode | `run(mock_answers=[...])` | Testability without live input; CI-friendly |
| Dual-format output | JSON + Markdown | Machine-readable for systems; human-readable for HR teams |

---

## 5. Output Formats

### JSON (`interview_<id>.json`)
Directly maps to `InterviewSession.model_dump()`. Suitable for:
- Ingestion into HRIS systems
- Storage in PostgreSQL / MongoDB / Databricks Delta tables
- Analytics pipelines and aggregated reporting

### Markdown (`interview_<id>_report.md`)
Human-readable HR report containing:
- Summary table (employee, department, session ID, date, status, overall sentiment)
- HR escalation banner (if red flags detected)
- Executive summary paragraph
- Bulleted HR recommendations
- Sentiment trace table with per-question scores
- Full annotated transcript (with 🚩 markers on flagged responses)

---

## 6. Configuration Reference (`config.yaml`)

| Key | Default | Description |
|---|---|---|
| `interview.company_name` | `"Acme Corp"` | Used in LLM prompts |
| `interview.max_follow_ups` | `2` | Max follow-up turns per session |
| `interview.model_interviewer` | `claude-haiku-4-5-20251001` | LLM for conversation |
| `interview.model_analyst` | `claude-sonnet-4-6` | LLM for analysis |
| `interview.temperature` | `0.7` | Creativity (0=deterministic) |
| `questions[].id` | required | Unique question identifier |
| `questions[].category` | required | Used in reports and analysis context |
| `questions[].required` | `true` | Whether to skip on partial interview |
| `red_flags.keywords` | list | Case-insensitive match triggers escalation |

---

## 7. Extending the System

### Add a new question
Edit `config.yaml` — add a new entry to `questions:`. No code change needed.

### Change the LLM
Edit `model_interviewer` or `model_analyst` in `config.yaml`. Any Anthropic model ID works.

### Add a new output format (e.g., CSV, HTML)
Add a `to_csv()` or `to_html()` static method to `ReportGenerator`. The `InterviewSession` Pydantic model handles all data access.

### Integrate with a database
After `orchestrator.run()`, call `session.model_dump()` and insert into any ORM/DB of your choice.

### Build a web UI
Replace the `input()` calls in `InterviewOrchestrator._get_answer()` with FastAPI WebSocket handlers. The rest of the system is UI-agnostic.
