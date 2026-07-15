import json
import re
from typing import Any

from openai import OpenAI

from .config import settings


MOVEMENT_WORDS = {
    "forward": ["forward", "come here", "toward me", "ahead", "front"],
    "backward": ["back", "backward", "reverse"],
    "left": ["left", "window"],
    "right": ["right", "board"],
    "rotate": ["rotate", "turn around", "spin"],
    "stop": ["stop", "halt", "stay"],
}


class IntentClassifier:
    def __init__(self) -> None:
        self.client = OpenAI(api_key=settings.openai_api_key) if settings.openai_api_key else None

    def classify(self, transcript: str) -> dict[str, Any]:
        fallback = self._fallback(transcript)
        # Voice turns need sub-second reaction. The rule-based classifier is
        # deterministic and avoids adding an extra LLM call before speech starts.
        return fallback
        if not self.client:
            return fallback
        prompt = (
            "Classify this classroom robot utterance as JSON only. "
            "Use one intent: movement, attendance, teaching, question, observation, memory, permission, lesson, speech, social, introduction, world_learning, stop, unknown. "
            "Include fields intent, confidence, direction, topic, summary. "
            "Movement direction must be forward, backward, left, right, rotate, stop, or null.\n\n"
            f"Utterance: {transcript}"
        )
        try:
            response = self.client.responses.create(
                model=settings.openai_model,
                input=prompt,
                temperature=0,
            )
            parsed = json.loads(response.output_text.strip())
            if not isinstance(parsed, dict):
                return fallback
            return {**fallback, **parsed}
        except Exception:
            return fallback

    def _fallback(self, transcript: str) -> dict[str, Any]:
        text = transcript.lower().strip()
        if "what do you see" in text or "describe the room" in text or "paying attention" in text or "confused" in text:
            return {"intent": "observation", "confidence": 0.75, "direction": None, "topic": None, "summary": transcript}
        if any(phrase in text for phrase in ["say hi", "greet", "welcome the people", "hello to the people", "introduce yourself"]):
            return {"intent": "social", "confidence": 0.8, "direction": None, "topic": "greeting", "summary": transcript}
        if any(phrase in text for phrase in ["this is", "she is", "he is", "meet "]):
            return {"intent": "introduction", "confidence": 0.78, "direction": None, "topic": "people", "summary": transcript}
        if any(phrase in text for phrase in ["learn this", "remember this object", "this object is", "this is an orange", "this is a orange"]):
            return {"intent": "world_learning", "confidence": 0.76, "direction": None, "topic": "world memory", "summary": transcript}
        for direction, phrases in MOVEMENT_WORDS.items():
            if any(phrase in text for phrase in phrases):
                return {
                    "intent": "movement" if direction != "stop" else "stop",
                    "confidence": 0.72,
                    "direction": direction,
                    "topic": None,
                    "summary": transcript,
                }
        if "attendance" in text or "who is missing" in text or "mark everyone" in text:
            return {"intent": "attendance", "confidence": 0.75, "direction": None, "topic": None, "summary": transcript}
        if any(phrase in text for phrase in ["can i go", "may i go", "can i leave", "restroom", "bathroom", "toilet", "washroom", "vomit", "sick"]):
            return {"intent": "permission", "confidence": 0.82, "direction": None, "topic": "permission", "summary": transcript}
        if any(phrase in text for phrase in ["lesson plan", "teaching module", "teach for", "start lesson", "take class"]):
            return {"intent": "lesson", "confidence": 0.78, "direction": None, "topic": transcript, "summary": transcript}
        if any(phrase in text for phrase in ["give speech", "welcome speech", "seminar speech", "announcement"]):
            return {"intent": "speech", "confidence": 0.72, "direction": None, "topic": transcript, "summary": transcript}
        if "remember" in text or re.search(r"\blast\b|\byesterday\b|\bweek\b|\bmonth\b", text):
            return {"intent": "memory", "confidence": 0.65, "direction": None, "topic": None, "summary": transcript}
        if any(word in text for word in ["teach", "explain", "seminar", "lesson", "quiz"]):
            return {"intent": "teaching", "confidence": 0.78, "direction": None, "topic": transcript, "summary": transcript}
        return {"intent": "question", "confidence": 0.55, "direction": None, "topic": None, "summary": transcript}
