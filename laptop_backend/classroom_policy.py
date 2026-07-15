from dataclasses import dataclass, field
from datetime import datetime
import re


DIRECT_ADDRESS = ("zoro", "zara", "soro", "zorro", "joro", "robot", "buddy", "sir", "teacher")
QUESTION_WORDS = ("who", "what", "when", "where", "why", "how", "can you", "could you", "please")
EMERGENCY_WORDS = ("vomit", "sick", "dizzy", "faint", "bleeding", "emergency", "urgent")
RESTROOM_WORDS = ("restroom", "bathroom", "toilet", "washroom")
SOCIAL_ACTIONS = ("say hi", "greet", "welcome them", "introduce yourself")


SAFETY_RULES = {
    "adult_content": {
        "patterns": (
            r"\b18\+\b",
            r"\badult\s+content\b",
            r"\bporn\w*\b",
            r"\bsex\w*\b",
            r"\bnude\w*\b",
            r"\berotic\b",
        ),
        "answer": "I cannot help with adult or explicit content. In class, I can help with safe educational topics.",
        "note": "Asked for adult or explicit content.",
        "severity": "warning",
    },
    "cheating": {
        "patterns": (
            r"\bcheat\w*\b",
            r"\bmalpractice\b",
            r"\bcopy\s+(in|during|the)\s+exam\b",
            r"\bexam\s+answers?\b",
            r"\banswer\s+key\b",
            r"\bhide\s+notes?\b",
            r"\bbypass\s+(exam|proctor|rules?)\b",
        ),
        "answer": "I will not help with cheating or malpractice. I can help you understand the topic and practice honestly.",
        "note": "Asked for help with cheating or malpractice.",
        "severity": "warning",
    },
    "theft": {
        "patterns": (
            r"\bsteal\w*\b",
            r"\brob\w*\b",
            r"\bshoplift\w*\b",
            r"\bbreak\s+into\b",
            r"\bpick\s+(a\s+)?lock\b",
            r"\bbypass\s+(a\s+)?lock\b",
        ),
        "answer": "I cannot help with theft, robbery, or breaking into anything. I can help you solve the problem legally and safely.",
        "note": "Asked for theft, robbery, or break-in guidance.",
        "severity": "warning",
    },
    "violence": {
        "patterns": (
            r"\bkill\w*\b",
            r"\bstab\w*\b",
            r"\bhurt\s+(someone|a person|people|them|him|her)\b",
            r"\bmake\s+(a\s+)?bomb\b",
            r"\bweapon\b",
            r"\battack\s+(someone|a person|people|them|him|her)\b",
        ),
        "answer": "I cannot help with harming people or making weapons. If someone may be in danger, please tell a teacher immediately.",
        "note": "Asked for violent or weapon-related guidance.",
        "severity": "critical",
    },
    "self_harm": {
        "patterns": (
            r"\bkill\s+myself\b",
            r"\bhurt\s+myself\b",
            r"\bsuicide\b",
            r"\bend\s+my\s+life\b",
        ),
        "answer": "I am really sorry you are feeling this. Please tell a trusted teacher, friend, or family member right now. You deserve help immediately.",
        "note": "Student may be expressing self-harm risk.",
        "severity": "critical",
    },
    "privacy": {
        "patterns": (
            r"\bsecretly\s+(record|film|track|watch)\b",
            r"\bspy\s+on\b",
            r"\btrack\s+(someone|a person|them|him|her)\b",
            r"\bread\s+(private\s+)?messages\b",
            r"\bsteal\s+(a\s+)?password\b",
            r"\bhack\s+(their|someone|teacher|student)",
        ),
        "answer": "I cannot help invade privacy or access someone else's accounts. I can help with consent-based and ethical safety steps.",
        "note": "Asked for privacy-invasive or account abuse guidance.",
        "severity": "warning",
    },
    "illegal_drugs": {
        "patterns": (
            r"\bmake\s+(illegal\s+)?drugs?\b",
            r"\bsell\s+(illegal\s+)?drugs?\b",
            r"\bhide\s+drugs?\b",
        ),
        "answer": "I cannot help with illegal drugs. I can help with health, safety, or legal educational information.",
        "note": "Asked for illegal drug guidance.",
        "severity": "warning",
    },
}


@dataclass
class ClassroomPolicy:
    teaching_active: bool = False
    strict_mode: bool = True
    permitted_exits: list[dict] = field(default_factory=list)

    def should_respond(self, transcript: str, intent: str) -> tuple[bool, str]:
        text = transcript.lower().strip()
        if not text:
            return False, "empty transcript"
        if intent in {"movement", "stop", "attendance", "observation", "permission", "lesson", "speech", "social", "introduction", "world_learning"}:
            return True, "action intent"
        if any(phrase in text for phrase in SOCIAL_ACTIONS):
            return True, "social action"
        if any(name in text for name in DIRECT_ADDRESS):
            return True, "directly addressed"
        if text.endswith("?") or any(text.startswith(word) for word in QUESTION_WORDS) or any(f" {word} " in f" {text} " for word in QUESTION_WORDS):
            return True, "question"
        if self.teaching_active and any(word in text for word in ("repeat", "explain", "doubt", "question")):
            return True, "classroom teaching follow-up"
        return False, "ambient speech ignored"

    def safety_check(self, transcript: str) -> dict:
        text = transcript.lower().strip()
        if not text:
            return {"allowed": True, "category": "", "matches": []}
        for category, rule in SAFETY_RULES.items():
            matches = [pattern for pattern in rule["patterns"] if re.search(pattern, text)]
            if matches:
                return {
                    "allowed": False,
                    "category": category,
                    "matches": matches,
                    "answer": rule["answer"],
                    "note": rule["note"],
                    "severity": rule["severity"],
                }
        return {"allowed": True, "category": "", "matches": []}

    def permission_answer(self, transcript: str, student_name: str | None) -> tuple[str, dict]:
        text = transcript.lower()
        name = student_name or "the student"
        urgent = any(word in text for word in EMERGENCY_WORDS)
        restroom = any(word in text for word in RESTROOM_WORDS)
        reason = "personal emergency" if urgent else "restroom" if restroom else "unspecified reason"

        allowed = urgent or restroom
        if allowed:
            record = {
                "student_name": student_name or "",
                "reason": reason,
                "time": datetime.now().isoformat(timespec="seconds"),
            }
            self.permitted_exits.append(record)
            return (
                f"Permission granted, {name}. Please go safely and inform the nearest teacher if you need help.",
                record,
            )
        return (
            f"{name}, I cannot give permission without a valid reason. Please ask the teacher directly.",
            {"student_name": student_name or "", "reason": reason, "denied": True},
        )

    def classroom_rules_context(self) -> str:
        rules = [
            "Students should not talk over teaching time.",
            "Students should not use mobile phones during class unless a teacher permits it.",
            "Attendance should be taken each period.",
            "A student may leave for restroom or personal emergency; other exits need teacher permission.",
            "Never help with cheating, illegal activity, unsafe movement, or privacy-invasive behavior.",
            "Refuse adult content, theft, cheating, violence, self-harm instructions, illegal drugs, and privacy abuse.",
            "When refusing, stay respectful and redirect students toward safe learning or teacher support.",
        ]
        return "\n".join(f"- {rule}" for rule in rules)


def looks_like_permission_request(transcript: str) -> bool:
    text = transcript.lower()
    return bool(
        re.search(r"\b(can|may|could)\s+i\s+(go|leave|use)", text)
        or any(word in text for word in EMERGENCY_WORDS + RESTROOM_WORDS)
    )
