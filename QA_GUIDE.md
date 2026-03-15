# Exit Interview Agent — Feature Q&A Guide

> Curated questions and answers explaining every design decision, feature, and architectural choice.
> Use this for presentations, code walkthroughs, and technical reviews.

---

## PART A — The Big Picture

---

**Q: What is this system and what problem does it solve?**

A: This is an Agentic AI system that automates exit interviews for HR teams. Traditionally, exit interviews are conducted by an HR manager, take significant time to schedule, and produce inconsistent data because different interviewers ask questions differently. This system deploys an AI agent that:
- Conducts a structured yet empathetic conversation
- Asks contextual follow-up questions based on what the employee just said
- Scores emotional tone on each response in real-time
- Flags legally sensitive language immediately
- Produces a structured JSON record (for systems) and a formatted Markdown report (for HR teams)

The result is consistent, scalable, always-available exit interviews with richer structured data than a human-conducted interview typically produces.

---

**Q: What makes this "agentic" rather than just a chatbot?**

A: Agentic AI implies the system takes autonomous, goal-directed actions — it doesn't just respond, it drives a workflow toward a defined outcome. This system is agentic because:
1. **It initiates** — the agent starts the conversation and owns the agenda
2. **It decides** — the Orchestrator decides whether to ask a follow-up, when to close, when to escalate
3. **It adapts** — follow-up questions are generated from the specific content of the employee's answer
4. **It reasons** — the Analyst Agent reads the full transcript and produces insights no template could produce
5. **It acts** — it writes structured outputs, flags escalations, and manages its own lifecycle via a state machine

---

**Q: Why Anthropic Claude instead of OpenAI GPT?**

A: The assignment lists several options including OpenAI. Claude was chosen because:
1. Claude has exceptional instruction-following which is critical for our structured output parsing (SUMMARY / RECOMMENDATIONS / OVERALL_SENTIMENT format)
2. Claude Haiku is highly competitive on latency and cost for conversational turns
3. Using separate tiers (Haiku for interview, Sonnet for analysis) is a professional cost-optimisation pattern
4. The Anthropic SDK is clean, well-documented, and supports modern Python (Pydantic-compatible)

The code is LLM-agnostic in architecture — swapping to GPT-4o requires only changing 2 lines in `config.yaml`.

---

## PART B — Architecture Decisions

---

**Q: Why two agents (InterviewerAgent + AnalystAgent) instead of one?**

A: This is the **separation of concerns** principle applied to AI systems — a best practice among senior AI architects.

- The Interviewer has one job: conduct a warm, natural conversation. It needs low latency (Haiku), a conversational temperature, and prompts tuned for empathy.
- The Analyst has one job: extract structured insights from a complete transcript. It needs higher reasoning capability (Sonnet), a more analytical prompt, and access to the full context including sentiment trace.

If you used one agent for both, you'd either over-spend on every conversational turn (using Sonnet) or under-invest on analysis (using Haiku). You'd also mix prompt concerns and make the system harder to tune and debug.

---

**Q: Why a Finite State Machine for the interview flow?**

A: Real production systems use state machines rather than procedural if/else chains because:

1. **Prevents impossible transitions** — the system can't jump from WARMUP to ANALYZING without going through QUESTIONING and CLOSING
2. **Auditability** — every state transition is logged, giving you a clear trace of what happened
3. **Graceful degradation** — if an exception fires mid-interview, the system knows its current state and can handle it correctly (partial reports instead of crashes)
4. **Extensibility** — adding a new state (e.g., `VERIFICATION` for identity check) requires one enum value and two transitions — not a refactor of the entire flow

---

**Q: Why are there two output formats (JSON and Markdown)?**

A: Different consumers need different formats.

- **JSON** is for systems — HRIS platforms, data pipelines, analytics dashboards, databases. It's the machine-readable source of truth.
- **Markdown** is for humans — HR business partners who want to read a report. It renders beautifully in GitHub, Notion, Confluence, and email clients.

Producing both from the same `InterviewSession` object costs essentially nothing and eliminates a whole class of manual work ("can you convert this to a readable report?").

---

## PART C — Feature-by-Feature

---

**Q: What is the Pydantic data model and why use it?**

A: Pydantic is Python's most popular data validation library. We use it for `ResponseRecord` and `InterviewSession` because:

1. **Type safety** — `sentiment_score: float = Field(ge=-1.0, le=1.0)` means if our scorer ever returns 1.5 (a bug), Pydantic raises an error immediately rather than silently corrupting data
2. **Auto-serialisation** — `session.model_dump()` produces a perfectly structured dict/JSON with zero manual mapping
3. **Self-documentation** — the model itself is the schema. Any developer reading `ResponseRecord` instantly knows every field, its type, and its constraints
4. **API integration** — Pydantic models are native to FastAPI, making it trivial to turn this into a web service later

Without Pydantic, you'd write dictionaries and hope nothing goes wrong. That's not production-grade.

---

**Q: What is real-time sentiment scoring and why is it done per-response rather than just at the end?**

A: Each response is scored for sentiment (very_positive → very_negative + a numeric score from -1 to +1) as it's captured — before the interview even ends.

Why per-response?
1. **Sentiment arc** — you can see emotional trajectory across the interview. Does the employee start neutral and become more negative as they open up? That pattern tells you something different than uniform negativity throughout.
2. **Analyst context** — the Analyst receives the sentiment trace as part of its input, so it can reference the arc in its summary ("sentiment shifted from neutral to negative when discussing management")
3. **Immediate value** — if the interview is abandoned midway, you still have sentiment data on every captured response

Why lexicon-based (keyword matching) rather than ML?
- Zero latency — no API call
- No extra dependency
- Fully sufficient for 6-response interview context
- The Analyst LLM produces the authoritative qualitative assessment anyway

---

**Q: What is the HR Red Flag Detector and why does it run inline?**

A: The detector scans every answer for a configurable list of keywords (harassment, discrimination, hostile, toxic, illegal, etc.). If found, it:
1. Prints an immediate terminal alert
2. Marks the `ResponseRecord.red_flag = True`
3. Sets `InterviewSession.red_flags_detected = True`
4. Tells the Analyst to include an escalation recommendation
5. Shows a 🚩 badge in the Markdown report

Why inline (not post-processing)?
- **Legal urgency**: if an employee mentions harassment, HR may need to act that day — not after the report is generated hours later
- **Auditability**: the flag is part of the immutable `ResponseRecord`, timestamped, and stored in JSON — it cannot be missed or deleted later

Why YAML-configurable keywords?
- HR and legal teams can update the list without touching code — appropriate separation of ownership

---

**Q: What is exponential backoff retry and why does the agent use it?**

A: When calling an LLM API, transient failures are common — rate limits, network blips, temporary server issues. Without retry logic, any of these crashes the entire interview, potentially losing all captured data.

Our `_call()` method retries up to 3 times with delays of 1s, 2s, 4s (exponential backoff). This handles:
- `RateLimitError` — too many requests in a short window
- `APIError` — transient server-side issues

Why exponential (not fixed) backoff?
- Fixed retry (retry every 1s) can make a rate limit situation worse by hammering the API
- Exponential backing off gives the API time to recover without you contributing to the problem

This is standard practice in any production service that calls external APIs.

---

**Q: Why is the question bank in YAML and not hardcoded?**

A: The YAML configuration file (`config.yaml`) is the single place HR teams can:
- Add, remove, or reorder interview questions
- Change the company name in all prompts
- Adjust max follow-ups
- Update the red-flag keyword list
- Switch LLM models

This enforces **correct ownership**: the content team / HR owns `config.yaml`, software engineers own the Python code. They never need to coordinate for content changes.

In large organisations, hardcoded content is a known source of toil — it requires a developer, a PR, code review, and a deployment just to change a question. YAML eliminates that entire workflow.

---

**Q: What is the Session UUID and why does every record have a timestamp?**

A: `session_id` is a `uuid4()` — a 128-bit random identifier unique across all sessions, systems, and time.

Why UUID?
- Enables correlation: the same `session_id` can be stored in your HRIS, your analytics DB, your audit log, and your S3 bucket — they all refer to the same interview
- No collision risk even at millions of sessions
- No sequential ID guessing (a security property)

Why ISO-8601 timestamps on every `ResponseRecord`?
- Immutable audit trail: you know exactly when each answer was given
- Enables time-based analysis: was there a long pause before a sensitive answer?
- Timezone-agnostic: ISO-8601 with UTC offset is unambiguous across regions

---

**Q: What is Demo/Mock Mode and why does it exist?**

A: Demo mode runs the entire interview pipeline with a pre-supplied list of answers instead of live `input()` prompts. You call it with `run(mock_answers=[...])`.

Why is this a professional practice?
1. **Testing** — you can run the full pipeline in CI/CD without a human. If it crashes, tests catch it.
2. **Demos** — you can showcase the system to stakeholders without a person typing live
3. **Reproducibility** — the same mock answers always produce the same output, making it easy to verify behaviour after code changes

Senior engineers always build testability into systems from day one. A system that can only be tested by a human typing live is not production-ready.

---

**Q: Why does the Analyst use a structured output format (SUMMARY / RECOMMENDATIONS / OVERALL_SENTIMENT)?**

A: LLMs produce free-form text by default, which is hard to parse reliably. By instructing the Analyst to use an exact template, we get:
- **Deterministic parsing**: split on fixed headers rather than hoping regex finds the right sentence
- **Complete output**: if any section is missing, parsing produces an empty field — a detectable, graceful failure
- **Consistent downstream consumption**: the Markdown report and JSON output depend on `summary`, `hr_recommendations`, and `overall_sentiment` being distinct fields

This is a pattern called **structured output prompting** — used in every serious LLM production system.

---

## PART D — Evaluation Criteria Alignment

---

**Q: How does this system demonstrate strong agentic design?**

A: Three hallmarks of agentic design are present:
1. **Autonomy** — the system completes the full workflow end-to-end without human direction
2. **Reactivity** — follow-up questions adapt to each specific answer; red flags trigger immediate alerts
3. **Goal-directedness** — the Orchestrator manages state toward a defined goal (complete interview + structured report)

The Finite State Machine is the clearest signal of agentic design rigour — it shows the system was designed, not just scripted.

---

**Q: How does this demonstrate LLM integration best practices?**

A:
- **Different models for different tasks** (Haiku vs Sonnet) — matched to task complexity and cost profile
- **Structured output prompting** on the Analyst — deterministic parsing
- **System/user message separation** — correct API usage
- **Retry with backoff** — production-grade resilience
- **Temperature control** — 0.7 for conversation warmth, deterministic enough for consistent outputs

---

**Q: How is code quality demonstrated?**

A:
- Every class and function has a docstring explaining *what* and *why* (not just *how*)
- Pydantic models make data contracts explicit and self-documenting
- State transitions are logged — the system is observable
- No magic strings — enums used for states and sentiment labels
- `load_config()` has a safe fallback — never hard-crashes on missing config
- The codebase is structured into 9 clearly separated sections (Configuration → Models → Scorer → State Machine → Agents → Orchestrator → Entry Point)
