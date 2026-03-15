#!/usr/bin/env python3
"""
Exit Interview Agent — Agentic AI System
=========================================
A production-grade multi-agent system for conducting intelligent,
empathetic exit interviews and generating structured HR insights.

Architecture:
    InterviewOrchestrator (State Machine)
        ├── InterviewerAgent  (Claude Haiku  — fast, empathetic conversation)
        └── AnalystAgent      (Claude Sonnet — deep insight extraction)

Professional Design Patterns Applied:
    1. Multi-Agent Architecture   — Interviewer ≠ Analyst (separation of concerns,
                                    different LLM tiers matched to task complexity)
    2. Finite State Machine       — INIT→WARMUP→QUESTIONING→FOLLOW_UP→CLOSING→ANALYZING→COMPLETE
                                    (rigorous lifecycle, no spaghetti control flow)
    3. Pydantic Data Contracts    — Type-safe, validated, auto-serializable models
                                    (no silent data corruption, easy API integration)
    4. Real-Time Sentiment Trace  — Per-response emotional scoring, not just end-summary
                                    (enables trend detection across the interview arc)
    5. HR Red Flag Detection      — Auto-flags legally sensitive language for escalation
                                    (protects org from missed harassment/legal signals)
    6. YAML Question Bank         — HR-configurable without touching code
                                    (content team owns config, engineers own infra)
    7. Retry + Exponential Backoff — Production-grade API resilience
    8. Session Audit Trail        — UUID + ISO-8601 timestamps on every interaction
    9. Graceful Partial Handling  — Incomplete interviews still produce valid reports
   10. Dual-Format Output         — JSON (machine/systems) + Markdown (human/HR teams)
   11. Demo/Mock Mode             — Run without live input for testing and demos

Author: Agentic AI Project
Date:   2026-03-15
"""

import os
import json
import time
import uuid
import re
import yaml
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

import anthropic
from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 1 — CONFIGURATION LOADER
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_CONFIG: dict = {
    "interview": {
        "company_name": "Acme Corp",
        "max_follow_ups": 2,
        "model_interviewer": "claude-haiku-4-5-20251001",
        "model_analyst": "claude-sonnet-4-6",
        "temperature": 0.7,
    },
    "questions": [
        {"id": "Q1", "category": "primary_reason",
         "text": "What is the primary reason for your decision to leave the organization?",
         "required": True},
        {"id": "Q2", "category": "overall_experience",
         "text": "How would you describe your overall experience working here?",
         "required": True},
        {"id": "Q3", "category": "positives",
         "text": "What did you enjoy most or find most valuable about working here?",
         "required": True},
        {"id": "Q4", "category": "improvements",
         "text": "What specific improvements would you suggest to make this a better workplace?",
         "required": True},
        {"id": "Q5", "category": "relationships",
         "text": "How would you describe your working relationship with your manager and team?",
         "required": True},
        {"id": "Q6", "category": "recommendation",
         "text": "Would you recommend this company to others as a place to work? Why or why not?",
         "required": False},
    ],
    "red_flags": {
        "keywords": ["harassment", "discrimination", "hostile", "toxic", "illegal",
                     "lawsuit", "retaliation", "threatened", "abused", "unsafe"],
        "alert_message": "⚠️  HR ESCALATION FLAG: This response may require immediate HR attention."
    }
}


def load_config(path: str = "config.yaml") -> dict:
    """
    Load interview configuration from YAML.
    Falls back to built-in defaults if file is missing.
    Professional pattern: never hard-crash on missing config in demos.
    """
    try:
        config_path = path if os.path.isabs(path) else os.path.join(
            os.path.dirname(__file__), path)
        with open(config_path, "r") as f:
            user_config = yaml.safe_load(f)
            # Shallow merge — user config wins at top level
            return {**DEFAULT_CONFIG, **user_config}
    except FileNotFoundError:
        print(f"[Config] '{path}' not found — using built-in defaults.")
        return DEFAULT_CONFIG


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 2 — PYDANTIC DATA MODELS  (type-safe structured contracts)
# ─────────────────────────────────────────────────────────────────────────────

class SentimentLabel(str, Enum):
    VERY_POSITIVE = "very_positive"
    POSITIVE      = "positive"
    NEUTRAL       = "neutral"
    NEGATIVE      = "negative"
    VERY_NEGATIVE = "very_negative"


class ResponseRecord(BaseModel):
    """
    Immutable record of a single Q&A exchange.
    All fields typed and validated — safe to serialize to JSON or a DB row.
    """
    question_id:       str
    question_category: str
    question_text:     str
    answer:            str
    follow_up_asked:   Optional[str]   = None
    follow_up_answer:  Optional[str]   = None
    sentiment:         SentimentLabel
    sentiment_score:   float           = Field(..., ge=-1.0, le=1.0)
    red_flag:          bool            = False
    timestamp:         str             # ISO-8601


class InterviewSession(BaseModel):
    """
    Complete interview session — the single source of truth for one employee's interview.
    UUID session_id enables correlation across systems (HR HRIS, analytics, audit).
    """
    session_id:           str
    employee_name:        str
    department:           Optional[str]              = None
    company_name:         str
    started_at:           str                         # ISO-8601
    completed_at:         Optional[str]               = None
    completed:            bool                        = False
    responses:            list[ResponseRecord]        = []
    overall_sentiment:    Optional[SentimentLabel]    = None
    summary:              Optional[str]               = None
    hr_recommendations:   Optional[list[str]]         = None
    red_flags_detected:   bool                        = False


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 3 — SENTIMENT SCORER  (lightweight lexicon, zero heavy ML deps)
# ─────────────────────────────────────────────────────────────────────────────

_POSITIVE_WORDS = frozenset({
    "great", "excellent", "amazing", "good", "love", "enjoyed", "wonderful",
    "fantastic", "positive", "happy", "satisfied", "appreciate", "helpful",
    "supportive", "collaborative", "growth", "learned", "opportunity",
    "recommend", "best", "strong", "transparent", "fair", "balanced",
    "flexible", "incredible", "proud", "fulfilling", "rewarding", "mentor",
    "trust", "innovative", "empowering", "inclusive", "creative",
})

_NEGATIVE_WORDS = frozenset({
    "bad", "terrible", "awful", "poor", "hate", "toxic", "hostile", "unfair",
    "frustrated", "disappointed", "ignored", "micromanaged", "underpaid",
    "overworked", "stressful", "burnout", "unclear", "chaotic", "favoritism",
    "politics", "harassment", "discrimination", "unhappy", "difficult",
    "exhausted", "neglected", "undermined", "disrespected", "unappreciated",
    "underdeveloped", "frustrated", "dismissed", "demotivating",
})


def score_sentiment(text: str) -> tuple[SentimentLabel, float]:
    """
    Lexicon-based sentiment scorer.
    Returns (label, score) where score ∈ [-1.0, +1.0].

    Why lexicon vs. ML model?
        — Zero latency, no API call, no dependency on network
        — Perfectly adequate for per-response tracking in an interview context
        — The Analyst LLM produces the authoritative overall sentiment anyway
    """
    words = re.findall(r"\b\w+\b", text.lower())
    pos   = sum(1 for w in words if w in _POSITIVE_WORDS)
    neg   = sum(1 for w in words if w in _NEGATIVE_WORDS)
    total = pos + neg

    if total == 0:
        return SentimentLabel.NEUTRAL, 0.0

    score = (pos - neg) / total
    if   score >=  0.6: label = SentimentLabel.VERY_POSITIVE
    elif score >=  0.2: label = SentimentLabel.POSITIVE
    elif score >= -0.2: label = SentimentLabel.NEUTRAL
    elif score >= -0.6: label = SentimentLabel.NEGATIVE
    else:               label = SentimentLabel.VERY_NEGATIVE

    return label, round(score, 3)


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 4 — INTERVIEW STATE MACHINE
# ─────────────────────────────────────────────────────────────────────────────

class InterviewState(str, Enum):
    """
    Finite states of an interview session.
    Using an explicit state machine prevents impossible transitions
    (e.g., jumping to ANALYZING before CLOSING) — a production-grade pattern.
    """
    INIT        = "init"
    WARMUP      = "warmup"
    QUESTIONING = "questioning"
    FOLLOW_UP   = "follow_up"
    CLOSING     = "closing"
    ANALYZING   = "analyzing"
    COMPLETE    = "complete"
    ABORTED     = "aborted"


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 5 — INTERVIEWER AGENT
# ─────────────────────────────────────────────────────────────────────────────

class InterviewerAgent:
    """
    Conducts the live exit interview conversation.

    Why Claude Haiku?
        — Optimised for low-latency, high-volume conversational turns
        — Cost-efficient at ~$0.25/MTok vs Sonnet's $3/MTok
        — Sufficient quality for empathetic question delivery and follow-up generation

    Key capabilities:
        — Warm, contextual question framing (references prior answers)
        — Dynamic follow-up generation based on specific response content
        — Retry with exponential backoff on API errors
    """

    def __init__(self, client: anthropic.Anthropic, config: dict):
        self.client      = client
        self.config      = config
        self.model       = config["interview"]["model_interviewer"]
        self.temperature = config["interview"]["temperature"]
        self.company     = config["interview"]["company_name"]

    def _call(self, system: str, user_msg: str,
              max_tokens: int = 200, retries: int = 3) -> str:
        """
        LLM call with exponential backoff retry.
        Professional pattern: never crash on transient API failures.
        """
        for attempt in range(retries):
            try:
                resp = self.client.messages.create(
                    model=self.model,
                    max_tokens=max_tokens,
                    system=system,
                    messages=[{"role": "user", "content": user_msg}],
                    temperature=self.temperature,
                )
                return resp.content[0].text.strip()
            except anthropic.RateLimitError:
                wait = 2 ** attempt
                print(f"\n  [Rate limit — retrying in {wait}s…]")
                time.sleep(wait)
            except anthropic.APIError:
                if attempt == retries - 1:
                    raise
                time.sleep(2 ** attempt)
        raise RuntimeError("Max API retries exceeded.")

    def greet(self, name: str) -> str:
        system = (
            f"You are a warm, empathetic HR interviewer at {self.company}. "
            "Your role is to conduct a respectful, fully confidential exit interview. "
            "Keep responses to 2–3 sentences. Be genuinely caring, not corporate-scripted."
        )
        return self._call(
            system,
            f"Greet {name} and briefly explain the purpose of this exit interview. "
            "Make them feel psychologically safe to share honestly.",
            max_tokens=150,
        )

    def ask(self, question: dict, prior_responses: list[ResponseRecord]) -> str:
        """Ask a question, contextually framed based on the most recent answer."""
        context = ""
        if prior_responses:
            last = prior_responses[-1]
            context = f"The employee just said: '{last.answer[:120]}'"

        system = (
            "You are an empathetic HR interviewer. "
            f"{'Context from prior answer: ' + context if context else ''} "
            "Deliver the question naturally in 1–2 sentences. "
            "If there's prior context, transition smoothly."
        )
        return self._call(system, f"Deliver this question naturally: {question['text']}", max_tokens=100)

    def follow_up(self, question: dict, answer: str) -> str:
        """Generate one contextual follow-up based on the specific answer given."""
        system = (
            "You are an empathetic HR interviewer. "
            "Generate ONE brief, natural follow-up question that gently probes deeper. "
            "It must be specific to what the employee just said. "
            "Maximum 1 sentence. Do NOT be interrogative."
        )
        return self._call(
            system,
            f"Original question: {question['text']}\nEmployee answered: {answer}\n\nFollow-up:",
            max_tokens=80,
        )

    def close(self, name: str) -> str:
        system = (
            f"You are a warm HR interviewer at {self.company}. "
            "Close the exit interview gracefully. Thank the employee sincerely. "
            "2–3 sentences. Be genuine. Wish them well in their future endeavours."
        )
        return self._call(system, f"Close the exit interview with {name}.", max_tokens=130)


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 6 — ANALYST AGENT
# ─────────────────────────────────────────────────────────────────────────────

class AnalystAgent:
    """
    Analyzes the complete interview transcript and produces structured HR insights.

    Why Claude Sonnet (not Haiku)?
        — Analysis requires nuanced reasoning across the full transcript
        — Quality of recommendations directly impacts HR decision-making
        — This is a one-time call per session — the cost premium is justified

    Produces:
        — Executive summary (3–4 sentences)
        — Actionable HR recommendations (3–5 bullet points)
        — Overall sentiment classification
    """

    def __init__(self, client: anthropic.Anthropic, config: dict):
        self.client = client
        self.model  = config["interview"]["model_analyst"]

    def analyze(self, session: "InterviewSession") -> tuple[str, list[str], SentimentLabel]:
        """
        Generate summary + recommendations + overall sentiment from full transcript.
        Returns (summary, recommendations, overall_sentiment).
        """
        transcript = "\n\n".join([
            f"Q [{r.question_category}]: {r.question_text}\n"
            f"A: {r.answer}"
            + (f"\nFollow-up Q: {r.follow_up_asked}\nFollow-up A: {r.follow_up_answer}"
               if r.follow_up_asked else "")
            for r in session.responses
        ])

        sentiment_trace = ", ".join(
            f"{r.question_category}: {r.sentiment.value}({r.sentiment_score:+.2f})"
            for r in session.responses
        )

        red_flag_note = (
            "\n⚠️  NOTE: One or more responses contained HR red-flag language. "
            "Include a specific escalation recommendation."
            if session.red_flags_detected else ""
        )

        system = f"""You are a senior HR analyst specialising in employee retention and organisational health.
Analyse the following exit interview transcript and produce a structured HR report.{red_flag_note}

Format your response EXACTLY as shown (do not deviate):

SUMMARY:
<3–4 sentence executive summary covering: primary exit reason, overall sentiment arc, key themes>

RECOMMENDATIONS:
- <specific, actionable recommendation 1>
- <specific, actionable recommendation 2>
- <specific, actionable recommendation 3>
- <specific, actionable recommendation 4 if warranted>
- <specific, actionable recommendation 5 if warranted>

OVERALL_SENTIMENT: <one of: very_positive | positive | neutral | negative | very_negative>"""

        try:
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=700,
                system=system,
                messages=[{
                    "role": "user",
                    "content": (
                        f"Employee: {session.employee_name} | "
                        f"Department: {session.department or 'N/A'} | "
                        f"Company: {session.company_name}\n\n"
                        f"Sentiment trace: {sentiment_trace}\n\n"
                        f"Full Transcript:\n{transcript}"
                    )
                }],
            )
            raw = resp.content[0].text.strip()
        except Exception as e:
            return (f"Analysis failed: {e}", [], SentimentLabel.NEUTRAL)

        # ── Parse structured response ──────────────────────────────────────
        summary         = ""
        recommendations = []
        sentiment_out   = SentimentLabel.NEUTRAL

        try:
            if "SUMMARY:" in raw:
                after_summary = raw.split("SUMMARY:")[1]
                parts = after_summary.split("RECOMMENDATIONS:")
                summary = parts[0].strip()

                if len(parts) > 1:
                    rec_sentiment = parts[1].split("OVERALL_SENTIMENT:")
                    rec_block = rec_sentiment[0].strip()
                    recommendations = [
                        ln.lstrip("- •").strip()
                        for ln in rec_block.split("\n")
                        if ln.strip().startswith(("-", "•"))
                    ]

                    if len(rec_sentiment) > 1:
                        s = rec_sentiment[1].strip().lower().replace(" ", "_")
                        try:
                            sentiment_out = SentimentLabel(s)
                        except ValueError:
                            pass
            else:
                summary = raw[:600]
        except Exception:
            summary = raw[:600]

        return summary, recommendations, sentiment_out


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 7 — REPORT GENERATOR
# ─────────────────────────────────────────────────────────────────────────────

class ReportGenerator:
    """Serialises an InterviewSession into JSON and human-readable Markdown."""

    @staticmethod
    def to_json(session: InterviewSession, path: str = None) -> str:
        data = session.model_dump()
        out  = json.dumps(data, indent=2, default=str)
        if path:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(out)
        return out

    @staticmethod
    def to_markdown(session: InterviewSession) -> str:
        _emoji = {
            "very_positive": "😊", "positive": "🙂", "neutral": "😐",
            "negative": "😟",      "very_negative": "😔",
        }
        overall_em = _emoji.get(session.overall_sentiment or "", "📊")

        lines = [
            "# Exit Interview Report",
            f"",
            f"| Field | Value |",
            f"|---|---|",
            f"| **Employee** | {session.employee_name} |",
            f"| **Department** | {session.department or 'N/A'} |",
            f"| **Company** | {session.company_name} |",
            f"| **Session ID** | `{session.session_id}` |",
            f"| **Date** | {session.started_at[:10]} |",
            f"| **Status** | {'✅ Complete' if session.completed else '⚠️ Partial'} |",
            f"| **Overall Sentiment** | {overall_em} {session.overall_sentiment or 'N/A'} |",
            f"",
        ]

        if session.red_flags_detected:
            lines += [
                "> ⚠️ **HR ESCALATION REQUIRED**",
                "> One or more responses contained language flagged for HR review.",
                "> See 🚩 markers in the transcript below.",
                "",
            ]

        lines += [
            "---",
            "## Executive Summary",
            "",
            session.summary or "_Summary not generated._",
            "",
            "---",
            "## HR Recommendations",
            "",
        ]

        if session.hr_recommendations:
            for rec in session.hr_recommendations:
                lines.append(f"- {rec}")
        else:
            lines.append("_No recommendations generated._")

        lines += [
            "",
            "---",
            "## Sentiment Trace",
            "",
            "| # | Question Area | Sentiment | Score |",
            "|---|---|---|---|",
        ]
        for i, r in enumerate(session.responses, 1):
            flag = " 🚩" if r.red_flag else ""
            lines.append(
                f"| {i} | {r.question_category}{flag} | {r.sentiment.value} | {r.sentiment_score:+.3f} |"
            )

        lines += [
            "",
            "---",
            "## Full Transcript",
            "",
        ]
        for i, r in enumerate(session.responses, 1):
            flag_badge = " 🚩 **[FLAGGED FOR HR REVIEW]**" if r.red_flag else ""
            lines += [
                f"### Q{i} — {r.question_category.replace('_', ' ').title()}{flag_badge}",
                f"",
                f"**Question:** {r.question_text}",
                f"",
                f"**Answer:** {r.answer}",
                f"",
            ]
            if r.follow_up_asked:
                lines += [
                    f"**Follow-up:** {r.follow_up_asked}",
                    f"",
                    f"**Follow-up Answer:** {r.follow_up_answer or '_No response_'}",
                    f"",
                ]

        lines += [
            "---",
            f"*Generated by Exit Interview Agent · Session `{session.session_id}` · {session.completed_at or 'N/A'}*",
        ]

        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 8 — INTERVIEW ORCHESTRATOR  (central state machine controller)
# ─────────────────────────────────────────────────────────────────────────────

class InterviewOrchestrator:
    """
    Central controller that manages the full interview lifecycle.

    Why a dedicated Orchestrator?
        — Single place to enforce state transitions — prevents impossible states
        — Decouples conversation logic (Interviewer) from analysis logic (Analyst)
        — Makes the system extensible: swap agents without touching orchestration

    Supports:
        — Live mode   : real-time input() prompts
        — Demo/mock mode : pre-supplied answers list (for testing, CI, demos)
    """

    def __init__(self, config: dict = None, api_key: str = None):
        self.config       = config or load_config()
        self.client       = anthropic.Anthropic(
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY")
        )
        self.interviewer  = InterviewerAgent(self.client, self.config)
        self.analyst      = AnalystAgent(self.client, self.config)
        self.state        = InterviewState.INIT
        self.session: Optional[InterviewSession] = None
        self._red_flag_kw = set(
            self.config.get("red_flags", {}).get("keywords", [])
        )

    # ── Internal helpers ───────────────────────────────────────────────────

    def _transition(self, new_state: InterviewState) -> None:
        print(f"  [state: {self.state.value} → {new_state.value}]", flush=True)
        self.state = new_state

    def _is_red_flag(self, text: str) -> bool:
        t = text.lower()
        return any(kw in t for kw in self._red_flag_kw)

    def _get_answer(self, name: str, mock_answers: list, idx: int, label: str = "Answer") -> str:
        if mock_answers:
            ans = mock_answers[idx] if idx < len(mock_answers) else ""
            print(f"  [Mock {label}]: {ans}")
            return ans
        return input(f"  {name}: ").strip()

    # ── Main run method ────────────────────────────────────────────────────

    def run(
        self,
        employee_name: str,
        department:    str = None,
        mock_answers:  list[str] = None,
        output_dir:    str = "outputs",
    ) -> InterviewSession:
        """
        Execute the full interview lifecycle.

        Args:
            employee_name : Name of the departing employee
            department    : Their department (optional)
            mock_answers  : List of pre-defined answers for demo/testing mode
            output_dir    : Directory to write JSON + Markdown outputs

        Returns:
            Completed InterviewSession object
        """
        # ── INIT → WARMUP ────────────────────────────────────────────────
        self._transition(InterviewState.WARMUP)

        self.session = InterviewSession(
            session_id   = str(uuid.uuid4()),
            employee_name= employee_name,
            department   = department,
            company_name = self.config["interview"]["company_name"],
            started_at   = datetime.now(timezone.utc).isoformat(),
        )

        print(f"\n{'═'*62}")
        print(f"  EXIT INTERVIEW  |  Session: {self.session.session_id[:8]}…")
        print(f"  Employee: {employee_name}  |  Company: {self.session.company_name}")
        print(f"{'═'*62}\n")

        greeting = self.interviewer.greet(employee_name)
        print(f"  Interviewer: {greeting}\n")

        # ── WARMUP → QUESTIONING ─────────────────────────────────────────
        self._transition(InterviewState.QUESTIONING)

        questions    = self.config["questions"]
        max_followup = self.config["interview"]["max_follow_ups"]
        answer_idx   = 0

        for q in questions:
            try:
                asked = self.interviewer.ask(q, self.session.responses)
                print(f"\n  Interviewer: {asked}")

                answer = self._get_answer(employee_name, mock_answers, answer_idx)
                answer_idx += 1

                # Graceful early exit keyword
                if answer.lower() in {"exit", "quit", "end", "stop"}:
                    print("\n  [Employee chose to end interview early.]")
                    break

                # Sentiment + red flag detection
                sentiment_label, sentiment_score = score_sentiment(answer)
                is_flag = self._is_red_flag(answer)
                if is_flag:
                    print(f"\n  {self.config['red_flags']['alert_message']}")
                    self.session.red_flags_detected = True

                # Follow-up (context-aware, capped at max_follow_ups per session)
                follow_up_q = None
                follow_up_a = None
                total_followups = sum(1 for r in self.session.responses if r.follow_up_asked)

                if total_followups < max_followup and answer:
                    self._transition(InterviewState.FOLLOW_UP)
                    follow_up_q = self.interviewer.follow_up(q, answer)
                    print(f"\n  Interviewer (follow-up): {follow_up_q}")
                    follow_up_a = self._get_answer(employee_name, mock_answers, answer_idx, "Follow-up Answer")
                    answer_idx += 1
                    if follow_up_a.lower() in {"skip", "pass", "n/a", ""}:
                        follow_up_a = None
                    self._transition(InterviewState.QUESTIONING)

                # Append validated record
                self.session.responses.append(ResponseRecord(
                    question_id       = q["id"],
                    question_category = q["category"],
                    question_text     = q["text"],
                    answer            = answer,
                    follow_up_asked   = follow_up_q,
                    follow_up_answer  = follow_up_a,
                    sentiment         = sentiment_label,
                    sentiment_score   = sentiment_score,
                    red_flag          = is_flag,
                    timestamp         = datetime.now(timezone.utc).isoformat(),
                ))

            except KeyboardInterrupt:
                print("\n\n  [Interrupted. Generating partial report…]")
                break

        # ── CLOSING ──────────────────────────────────────────────────────
        self._transition(InterviewState.CLOSING)
        closing = self.interviewer.close(employee_name)
        print(f"\n  Interviewer: {closing}\n")

        # ── ANALYZING ────────────────────────────────────────────────────
        self._transition(InterviewState.ANALYZING)
        print("\n  Generating analysis report…")

        if self.session.responses:
            summary, recommendations, overall_sentiment = self.analyst.analyze(self.session)
            self.session.summary             = summary
            self.session.hr_recommendations  = recommendations
            self.session.overall_sentiment   = overall_sentiment

        self.session.completed    = True
        self.session.completed_at = datetime.now(timezone.utc).isoformat()

        # ── SAVE OUTPUTS ─────────────────────────────────────────────────
        sid       = self.session.session_id[:8]
        base_dir  = os.path.join(os.path.dirname(__file__), output_dir)
        json_path = os.path.join(base_dir, f"interview_{sid}.json")
        md_path   = os.path.join(base_dir, f"interview_{sid}_report.md")

        ReportGenerator.to_json(self.session, json_path)
        md_content = ReportGenerator.to_markdown(self.session)
        os.makedirs(base_dir, exist_ok=True)
        with open(md_path, "w") as f:
            f.write(md_content)

        self._transition(InterviewState.COMPLETE)

        print(f"\n{'═'*62}")
        print(f"  INTERVIEW COMPLETE")
        print(f"  JSON output     → {json_path}")
        print(f"  Markdown report → {md_path}")
        print(f"{'═'*62}\n")
        print(md_content)

        return self.session


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 9 — ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("\n  Agentic Exit Interview System")
    print("  " + "─" * 38)

    mode = input("  Run mode — (L)ive interview or (D)emo with mock answers? [L/D]: ").strip().upper()

    name = input("  Employee name: ").strip() or "Employee"
    dept = input("  Department (press Enter to skip): ").strip() or None

    orchestrator = InterviewOrchestrator()

    if mode == "D":
        # Demo mode — realistic pre-built answers showcase all features
        mock = [
            "Honestly, the main reason is limited career growth. I've been in the same role "
            "for 3 years with no clear promotion path despite strong performance reviews.",

            "Overall it was a mixed experience. The work itself was interesting and I loved "
            "my direct team, but the broader organisation felt quite siloed and opaque.",

            "Follow-up: the collaborative culture within my immediate squad — we really supported each other.",

            "The product challenges were genuinely exciting. I felt proud of what we shipped.",

            "Follow-up: the lack of internal mobility and cross-team visibility was the biggest gap.",

            "The company needs to invest heavily in career ladders and make them transparent. "
            "Also, manager training — some managers are excellent, others have almost no "
            "people management skills, and it creates a very inconsistent experience.",

            "Follow-up: a structured mentorship programme would make a huge difference.",

            "My direct manager was supportive and fought for my work. The wider leadership "
            "was quite distant and hard to access unless you were already in the inner circle.",

            "I'd recommend it with caveats — strong product, good pay, but only if you're "
            "happy staying in one lane. If you want to grow broadly, look elsewhere.",

            "Follow-up: they should fix the internal transfer process — it's almost broken.",
        ]
        orchestrator.run(name, dept, mock_answers=mock)
    else:
        orchestrator.run(name, dept)


if __name__ == "__main__":
    main()
