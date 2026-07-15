import asyncio
from typing import Any

from .ai_teacher import TeacherAI
from .attendance import AttendanceService
from .behavior import BehaviorStore
from .classroom_policy import ClassroomPolicy
from .intent import IntentClassifier
from .lesson_planner import LessonPlanner
from .memory import MemoryStore
from .notifications import NotificationStore
from .people_memory import PeopleMemory
from .perception import PerceptionEngine
from .rag import RagIndex
from .robot_client import RobotClient
from .self_model import SelfModel
from .world_memory import WorldMemory
from .voice import VoicePipeline


VOICE_MOVE_PROFILE = {
    "forward": {"speed": 0.55, "seconds": 2.0, "label": "about one metre"},
    "backward": {"speed": 0.45, "seconds": 0.75, "label": "about thirty centimetres"},
    "left": {"speed": 0.5, "seconds": 0.45, "label": "a short left turn"},
    "right": {"speed": 0.5, "seconds": 0.45, "label": "a short right turn"},
    "rotate": {"speed": 0.45, "seconds": 1.0, "label": "a short scan turn"},
}


class ZoroBrain:
    def __init__(
        self,
        teacher: TeacherAI,
        robot: RobotClient,
        attendance: AttendanceService,
        voice: VoicePipeline,
        memory: MemoryStore,
        self_model: SelfModel,
        perception: PerceptionEngine,
        notifications: NotificationStore | None = None,
        policy: ClassroomPolicy | None = None,
        lessons: LessonPlanner | None = None,
        behavior: BehaviorStore | None = None,
        people_memory: PeopleMemory | None = None,
        rag: RagIndex | None = None,
        world_memory: WorldMemory | None = None,
    ) -> None:
        self.teacher = teacher
        self.robot = robot
        self.attendance = attendance
        self.voice = voice
        self.memory = memory
        self.self_model = self_model
        self.perception = perception
        self.intent = IntentClassifier()
        self.notifications = notifications or NotificationStore()
        self.policy = policy or ClassroomPolicy()
        self.lessons = lessons or LessonPlanner()
        self.behavior = behavior or BehaviorStore()
        self.people_memory = people_memory or PeopleMemory()
        self.rag = rag or RagIndex()
        self.world_memory = world_memory or WorldMemory()

    async def handle_transcript(self, transcript: str, student_name: str | None = None) -> dict[str, Any]:
        classification = self.intent.classify(transcript)
        intent = classification.get("intent", "question")
        safety = self.policy.safety_check(transcript)
        if not safety.get("allowed", True):
            answer = str(safety.get("answer") or "I cannot help with that request. I can help with safe learning instead.")
            event = self.behavior.add_event(
                student_name,
                "rule_violation",
                str(safety.get("note") or "Asked for unsafe or disallowed help."),
                str(safety.get("severity") or "warning"),
                {
                    "transcript": transcript,
                    "category": safety.get("category", ""),
                    "matches": safety.get("matches", []),
                },
            )
            self.notifications.add("safety_refusal", answer, str(safety.get("severity") or "warning"), event)
            return {
                "transcript": transcript,
                "intent": {**classification, "safety_refusal": safety.get("category", "")},
                "ignored": False,
                "attention_reason": "safety refusal",
                "answer": answer,
                "audio_path": None,
                "audio_url": None,
                "audio_bytes": None,
            }
        profanity = self.behavior.detect_profanity(transcript)
        if profanity:
            answer = self._behavior_warning(student_name)
            event = self.behavior.add_event(
                student_name,
                "profanity",
                "Used disrespectful or profane language toward Zoro or in class.",
                "warning",
                {"transcript": transcript, "matches": profanity},
            )
            self.notifications.add("behavior_warning", answer, "warning", event)
            return {
                "transcript": transcript,
                "intent": {**classification, "behavior_event": "profanity"},
                "ignored": False,
                "attention_reason": "behavior warning",
                "answer": answer,
                "audio_path": None,
                "audio_url": None,
                "audio_bytes": None,
            }
        should_respond, attention_reason = self.policy.should_respond(transcript, intent)
        if not should_respond:
            if self.policy.teaching_active and len(transcript.split()) >= 5:
                self.behavior.add_event(
                    student_name,
                    "unwanted_talk",
                    "Spoke during active teaching time without addressing Zoro.",
                    "warning",
                    {"transcript": transcript},
                )
            return {
                "transcript": transcript,
                "intent": classification,
                "ignored": True,
                "attention_reason": attention_reason,
                "answer": "",
                "audio_path": None,
                "audio_url": None,
                "audio_bytes": None,
            }

        if intent in {"movement", "stop"}:
            answer = await self._movement_answer(classification)
        elif intent == "attendance":
            answer = await self._attendance_answer(transcript)
        elif intent == "permission":
            answer, permission = self.policy.permission_answer(transcript, student_name)
            severity = "warning" if permission.get("denied") else "info"
            self.notifications.add("permission", answer, severity, permission)
        elif intent == "lesson":
            plan = self.lessons.start_or_resume(classification.get("topic") or "", 30, 2)
            self.policy.teaching_active = True
            progress = plan.get("progress") or {}
            current = progress.get("current_item") or {}
            answer = (
                f"I prepared a {plan['duration_minutes']}-minute {plan['subject']} lesson. "
                f"I will continue from {current.get('title') or 'the beginning'} and stay inside the uploaded syllabus."
            )
        elif intent == "observation":
            answer = self.perception.short_view_answer()
        elif intent == "social":
            answer = self._social_answer(transcript, student_name)
        elif intent == "introduction":
            answer = self._introduction_answer(transcript)
        elif intent == "world_learning":
            answer = self._world_learning_answer(transcript)
        elif intent == "memory":
            memories = self._combined_memory_context(transcript)
            lesson_context = self._lesson_context()
            rag_context = self.rag.context_for(transcript + "\n" + lesson_context, self._active_subject())
            answer = self.teacher.answer_with_context(
                transcript, student_name,
                self.self_model.context(), self.perception.context(), memories,
                self.policy.classroom_rules_context(),
                rag_context,
                lesson_context,
            )
        else:
            instant = self._instant_answer(transcript)
            if instant:
                answer = instant
            else:
                memories = self._combined_memory_context(transcript)
                lesson_context = self._lesson_context()
                if self.policy.teaching_active:
                    quick = self.rag.quick_answer(transcript + "\n" + lesson_context, self._active_subject())
                    if quick.get("answered"):
                        answer = str(quick.get("answer") or "").strip()
                    else:
                        answer = "That is not covered clearly in the uploaded subject material. Please ask the teacher if you want to go outside today's syllabus."
                else:
                    answer = self.teacher.answer_with_context(
                        transcript, student_name,
                        self.self_model.context(), self.perception.context(), memories,
                        self.policy.classroom_rules_context(),
                        "__NO_RAG__",
                        "",
                    )

        topic = classification.get("topic") or classification.get("summary") or intent
        self.self_model.remember_interaction(student_name, topic)
        self.memory.add(
            "conversation",
            f"Student: {student_name or 'unknown'}\nQuestion: {transcript}\nAnswer: {answer}",
            {"intent": intent, "student_name": student_name or ""},
        )
        return {
            "transcript": transcript,
            "intent": classification,
            "ignored": False,
            "attention_reason": attention_reason,
            "answer": answer,
            "audio_path": None,
            "audio_url": None,
            "audio_bytes": None,
        }

    async def handle_transcript_streaming(
        self,
        transcript: str,
        student_name: str | None = None,
        tts_callback=None,
    ) -> dict[str, Any]:
        """Like handle_transcript but streams TTS sentence by sentence."""
        classification = self.intent.classify(transcript)
        intent = classification.get("intent", "question")
        safety = self.policy.safety_check(transcript)
        if not safety.get("allowed", True):
            return await self.handle_transcript(transcript, student_name)
        profanity = self.behavior.detect_profanity(transcript)
        if profanity:
            return await self.handle_transcript(transcript, student_name)
        should_respond, attention_reason = self.policy.should_respond(transcript, intent)
        if not should_respond:
            return {
                "transcript": transcript,
                "intent": classification,
                "ignored": True,
                "attention_reason": attention_reason,
                "answer": "",
                "audio_path": None,
                "audio_url": None,
                "audio_bytes": None,
            }

        if intent in {"movement", "stop"}:
            full_answer = await self._movement_answer(classification, wait_for_completion=False)
            if tts_callback and full_answer:
                await tts_callback(full_answer, True)
            return {
                "transcript": transcript,
                "intent": classification,
                "ignored": False,
                "attention_reason": attention_reason,
                "answer": full_answer,
                "audio_path": None,
                "audio_url": None,
                "audio_bytes": None,
            }

        if intent == "observation":
            answer = self.perception.short_view_answer()
            if tts_callback:
                await tts_callback(answer, True)
            return {
                "transcript": transcript,
                "intent": classification,
                "ignored": False,
                "attention_reason": attention_reason,
                "answer": answer,
                "audio_path": None,
                "audio_url": None,
                "audio_bytes": None,
            }

        if intent == "attendance":
            if tts_callback:
                await tts_callback("I am going to take attendance now. Please keep your face up and stay still.", False)
            result = await self.handle_transcript(transcript, student_name)
            if tts_callback and result.get("answer"):
                await tts_callback(str(result["answer"]), True)
            result["audio_path"] = None
            result["audio_url"] = None
            result["audio_bytes"] = None
            return result

        # For these action intents, use the regular action path, then speak through live TTS.
        if intent in {"social", "introduction", "world_learning"}:
            result = await self.handle_transcript(transcript, student_name)
            if tts_callback and result.get("answer"):
                await tts_callback(str(result["answer"]), True)
            result["audio_path"] = None
            result["audio_url"] = None
            result["audio_bytes"] = None
            return result

        memories = self._combined_memory_context(transcript)
        lesson_context = self._lesson_context()
        instant = self._instant_answer(transcript)
        if instant:
            if tts_callback:
                await tts_callback(instant, True)
            return {
                "transcript": transcript,
                "intent": {**classification, "instant": True},
                "ignored": False,
                "attention_reason": attention_reason,
                "answer": instant,
                "audio_path": None,
                "audio_url": None,
                "audio_bytes": None,
            }
        if self.policy.teaching_active:
            quick = self.rag.quick_answer(transcript + "\n" + lesson_context, self._active_subject())
            if quick.get("answered"):
                full_answer = str(quick.get("answer") or "").strip()
                if tts_callback and full_answer:
                    await tts_callback(full_answer, True)
                topic = classification.get("topic") or classification.get("summary") or intent
                self.self_model.remember_interaction(student_name, topic)
                self.memory.add(
                    "conversation",
                    f"Student: {student_name or 'unknown'}\nQuestion: {transcript}\nAnswer: {full_answer}",
                    {"intent": intent, "student_name": student_name or "", "rag_fast": "true", "teaching_mode": "true"},
                )
                return {
                    "transcript": transcript,
                    "intent": {**classification, "rag_fast": True, "teaching_mode": True},
                    "ignored": False,
                    "attention_reason": attention_reason,
                    "answer": full_answer,
                    "audio_path": None,
                    "audio_url": None,
                    "audio_bytes": None,
                }
            full_answer = "That is not covered clearly in the uploaded subject material."
            if tts_callback:
                await tts_callback(full_answer, True)
            return {
                "transcript": transcript,
                "intent": {**classification, "rag_fast": False, "outside_material": True, "teaching_mode": True},
                "ignored": False,
                "attention_reason": attention_reason,
                "answer": full_answer,
                "audio_path": None,
                "audio_url": None,
                "audio_bytes": None,
            }
        lesson_context = ""
        rag_context = "__NO_RAG__"
        full_answer_parts: list[str] = []
        first_sentence = True
        thinking_task = None
        thinking_state = {"played": False, "cancel": False}
        filler = self._thinking_filler(transcript)

        async def delayed_thinking_filler() -> None:
            await asyncio.sleep(0.02)
            if thinking_state["cancel"]:
                return
            thinking_state["played"] = True
            full_answer_parts.append(filler)
            await tts_callback(filler, True)

        if filler and tts_callback:
            thinking_task = asyncio.create_task(delayed_thinking_filler())

        async for sentence in self.teacher.stream_answer(
            transcript, student_name,
            self.self_model.context(), self.perception.context(), memories,
            self.policy.classroom_rules_context(),
            rag_context,
            lesson_context,
        ):
            full_answer_parts.append(sentence)
            if tts_callback:
                try:
                    if thinking_task is not None and not thinking_task.done():
                        thinking_state["cancel"] = True
                        thinking_task.cancel()
                    if thinking_task is not None and thinking_task.done():
                        await thinking_task
                    if thinking_state["played"]:
                        first_sentence = False
                    thinking_task = None
                    await tts_callback(sentence, first_sentence)
                    first_sentence = False
                except Exception as exc:
                    print(f"[Streaming TTS] sentence failed: {exc}")
        if thinking_task is not None and not thinking_task.done():
            thinking_state["cancel"] = True
            thinking_task.cancel()

        full_answer = " ".join(full_answer_parts)
        topic = classification.get("topic") or classification.get("summary") or intent
        self.self_model.remember_interaction(student_name, topic)
        self.memory.add(
            "conversation",
            f"Student: {student_name or 'unknown'}\nQuestion: {transcript}\nAnswer: {full_answer}",
            {"intent": intent, "student_name": student_name or ""},
        )
        return {
            "transcript": transcript,
            "intent": classification,
            "ignored": False,
            "attention_reason": attention_reason,
            "answer": full_answer,
            "audio_path": None,
            "audio_url": None,
            "audio_bytes": None,
        }

    async def _synthesize_sentence(self, sentence: str) -> bytes:
        """Fetch Deepgram TTS bytes for a single sentence."""
        import httpx
        from .config import settings

        url = "https://api.deepgram.com/v1/speak"
        params = {"model": settings.deepgram_tts_model}
        headers = {
            "Authorization": f"Token {settings.deepgram_api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                url, params=params, headers=headers,
                json={"text": sentence},
            )
            response.raise_for_status()
            return response.content

    def _instant_answer(self, transcript: str) -> str:
        text = transcript.lower().strip(" .?!")
        if not text:
            return ""
        if "1983" in text and "indian cricket" in text and "captain" in text:
            return "Kapil Dev was the captain of the Indian cricket team in 1983."
        if text in {"zoro", "hey zoro", "hello zoro", "hi zoro", "buddy"}:
            return "Yes, I am listening."
        if "your name" in text or "who are you" in text or "what are you" in text:
            return "I am Zoro 2026, your classroom teaching robot."
        if ("who made you" in text or "who created you" in text or "your creators" in text) and "why" in text:
            return (
                "I was created by Abishek, Ubaith sherif, Kishore Kumar, and Haroon Bashi, "
                "with grateful guidance from Kowsalya ma'am, our project mentor. "
                "They created me to help students learn, support teachers in the classroom, and make education more friendly and interactive."
                " Thank you ma'am for your guidance."
            )
        if "who made you" in text or "who created you" in text or "your creators" in text:
            return (
                "I was created by Abishek, Ubaith sherif, Kishore Kumar, and Haroon Bashi, "
                "with grateful guidance from Kowsalya ma'am, our project mentor. Thank you ma'am for your guidance."
            )
        if "can you hear" in text:
            return "Yes, I can hear you through my microphone."
        return ""

    def _thinking_filler(self, transcript: str) -> str:
        text = transcript.lower().strip()
        if text.startswith(("what", "who", "when", "where", "why", "how", "can", "could", "do", "does", "is", "are", "tell me", "explain")):
            return "Yeah."
        if "?" in text:
            return "Yeah."
        return ""

    async def _complete_bounded_movement(self, direction: str, speed: float, seconds: float) -> None:
        try:
            await asyncio.sleep(seconds)
            await self.robot.stop()
            self.self_model.update_motion("stop")
            self.perception.update_motion_estimate("stop", 0.0)
        except Exception as exc:
            print(f"Warning: bounded movement stop failed: {exc}")

    async def _movement_answer(self, classification: dict[str, Any], wait_for_completion: bool = True) -> str:
        direction = classification.get("direction") or "stop"
        if direction == "stop":
            await self.robot.stop()
            self.self_model.update_motion("stop")
            return "Okay, I am stopping and staying right here."

        clear, reason = self.perception.movement_clear(direction)
        if not clear:
            await self.robot.stop()
            self.self_model.update_motion("blocked")
            self.perception.update_motion_estimate("stop", 0.0)
            blocker = self.perception.blockage_for_direction(direction)
            if blocker and blocker.get("label") == "person":
                return "Please step aside for a moment. I need to go forward, and I do not want to bump into you."
            options = [item for item in self.perception.clear_directions() if item != direction]
            if blocker:
                if options:
                    return f"Obstacle detected. I cannot move {direction} safely. I will look {options[0]} for a clearer path."
                return f"Obstacle detected. I cannot move {direction} safely, so I am stopping and looking around."
            return reason

        profile = VOICE_MOVE_PROFILE.get(direction, {"speed": 0.55, "seconds": 0.75, "label": direction})
        speed = float(profile["speed"])
        seconds = float(profile["seconds"])
        await self.robot.move(direction, speed)
        self.self_model.update_motion(direction)
        self.perception.update_motion_estimate(direction, speed)
        if wait_for_completion:
            await self._complete_bounded_movement(direction, speed, seconds)
        else:
            asyncio.create_task(self._complete_bounded_movement(direction, speed, seconds))
            if direction == "rotate":
                return "Okay, I am making a short scan turn."
            if direction in {"left", "right"}:
                return f"Okay, turning {direction} a little."
            return f"Okay, moving {profile['label']}."
        if direction == "rotate":
            return "Okay, I made a short scan turn and stopped."
        if direction in {"left", "right"}:
            return f"Okay, I turned {direction} a little and stopped."
        return f"Okay, I moved {profile['label']} and stopped."

    async def _attendance_answer(self, transcript: str) -> str:
        if "mark everyone" in transcript.lower():
            for name in self.attendance.known_names:
                self.attendance.mark(name)
            return (
                f"I marked everyone in my known face list present: "
                f"{', '.join(self.attendance.known_names) or 'no enrolled students yet'}."
            )
        if self.perception.latest_jpeg:
            result = self.attendance.recognize_and_mark(self.perception.latest_jpeg)
            marked = result.get("marked", [])
            if marked:
                return f"Attendance marked for: {', '.join(marked)}."
            return "I scanned the room, but I did not confidently recognize any enrolled students."
        return "I do not have a fresh camera frame yet, so I cannot take attendance honestly."

    def _behavior_warning(self, student_name: str | None) -> str:
        name = student_name or "student"
        return f"{name}, please keep respectful language in class. I have marked this as a behavior warning."

    def _social_answer(self, transcript: str, student_name: str | None) -> str:
        text = transcript.lower()
        if "introduce yourself" in text:
            return (
                "Hello everyone, I am Zoro 2026, a classroom teaching robot created by Abishek, Ubaith sherif, "
                "Kishore Kumar, and Haroon Bashi, with grateful guidance from Kowsalya ma'am, our project mentor. "
                "Thank you ma'am for your guidance. "
                "My laptop brain helps me teach from your syllabus, answer doubts, "
                "watch the classroom respectfully, take attendance, notice safety risks, and speak through my speaker. "
                "My camera is my eyes, my microphone is my ears, my display is my face, and my wheels are my legs. "
                "I am here to be a friendly faculty member: clear, patient, honest, non-violent, and useful without ever "
                "helping with cheating, harm, or illegal things."
            )
        recognized = [face.get("name") for face in self.perception.state.faces if face.get("name")]
        if recognized:
            self.people_memory.touch_seen(recognized)
            greeting = self.people_memory.greeting_for(recognized)
            if greeting:
                return greeting
            names = ", ".join(recognized[:4])
            return f"Hi {names}. Good to see you all. How are you doing today?"
        people = len([obj for obj in self.perception.state.objects if obj.get("label") == "person"])
        if people >= 2:
            return "Hello everyone. Good to see you all. How are you doing today?"
        if people == 1:
            return "Hello sir or madam. Good to see you. How are you doing today?"
        return "Hello everyone. I am ready when you are."

    def _introduction_answer(self, transcript: str) -> str:
        people = self.people_memory.parse_introductions(transcript)
        if not people:
            return "I heard the introduction, but I could not clearly find the names. Please say, this is Kowsalya ma'am, our HOD."
        result = self.people_memory.enroll_from_jpeg(self.perception.latest_jpeg, people)
        try:
            self.attendance.reload_faces()
        except Exception:
            pass
        names = []
        for person in result["enrolled"]:
            role = person.get("role")
            names.append(f"{person['name']} ({role})" if role else person["name"])
        return f"I will remember {', '.join(names)}. I will recognize and greet them when I see them again."

    def _world_learning_answer(self, transcript: str) -> str:
        lowered = transcript.lower()
        name = ""
        facts = ""
        for marker in ("this object is", "this is an", "this is a", "learn this", "remember this object"):
            if marker in lowered:
                tail = transcript[lowered.index(marker) + len(marker):].strip(" .,:;")
                if "." in tail:
                    name, facts = tail.split(".", 1)
                elif " and " in tail:
                    name, facts = tail.split(" and ", 1)
                else:
                    name = tail
                break
        if not name:
            visible = [obj.get("label") for obj in self.perception.state.objects if obj.get("label") and obj.get("label") != "person"]
            name = visible[0] if visible else ""
        if not name:
            return "I can learn it, but I need a clear name. Please say, this object is an orange."
        try:
            item = self.world_memory.teach(name, facts, "voice")
        except ValueError as exc:
            return str(exc)
        return f"I learned {item['name']}. I will remember it when I see or hear about it again."

    def _active_subject(self) -> str:
        progress = self.lessons.progress().get("items", [])
        active = next((item for item in progress if item and item.get("status") == "active"), None)
        return (active or {}).get("subject", "")

    def _lesson_context(self) -> str:
        progress = self.lessons.progress().get("items", [])
        active = next((item for item in progress if item and item.get("status") == "active"), None)
        if not active:
            return "No active lesson."
        current = active.get("current_item") or {}
        topics = ", ".join(current.get("topics") or [])
        return (
            f"Subject: {active.get('subject')}\n"
            f"Status: {active.get('status')}\n"
            f"Current segment: {current.get('title') or 'Completed'}\n"
            f"Topics now: {topics or 'not specified'}\n"
            f"Completed minutes: {active.get('completed_minutes')}\n"
            f"Remaining minutes: {active.get('remaining_minutes')}"
        )

    def _combined_memory_context(self, query: str) -> str:
        return (
            f"Long-term memories:\n{self.memory.context_for(query)}\n\n"
            f"World observations and hypotheses:\n{self.world_memory.context_for(query)}"
        )
