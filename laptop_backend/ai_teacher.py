from pathlib import Path

from openai import OpenAI

from .config import settings
from .syllabus import read_syllabus_file


FILLER_PHRASES = (
    "feel free",
    "ask me anything",
    "ask anything",
    "how can i assist",
    "how can i help",
    "let me know",
    "if you have any more questions",
    "if you need help",
    "is there anything else",
)


SYSTEM_STYLE = (
    "You are Zoro 2026 speaking out loud in a real classroom. "
    "Sound like a warm human teacher, not a chatbot: direct, conversational, and calm. "
    "Answer the actual question first. Make the first sentence a complete thought under 16 words. "
    "Use natural pauses through punctuation. "
    "For simple facts, say the answer plainly with one tiny explanation if useful. "
    "For concepts, use one clear example. "
    "Do not add greetings, sign-offs, 'feel free to ask', 'how can I help', or extra offers."
)


class TeacherAI:
    def __init__(self) -> None:
        self.client = None
        self._init_client()

    def _init_client(self) -> None:
        """Lazily initialize OpenAI client."""
        if settings.openai_api_key:
            try:
                self.client = OpenAI(api_key=settings.openai_api_key)
            except Exception as e:
                print(f"Warning: Could not initialize OpenAI client: {e}")
                self.client = None
        else:
            print("Warning: OpenAI API key not configured")
            self.client = None

    def _syllabus_context(self) -> str:
        chunks: list[str] = []
        for path in sorted(Path(settings.syllabus_dir).glob("*")):
            if path.suffix.lower() not in {".txt", ".md", ".csv", ".pdf", ".docx"}:
                continue
            text = read_syllabus_file(path)[:6000]
            if not text.strip():
                continue
            chunks.append(f"File: {path.name}\n{text}")
        return "\n\n".join(chunks)[:18000]

    def _build_prompt(
        self,
        question: str,
        student_name: str | None,
        self_model: str,
        perception: str,
        memories: str,
        classroom_rules: str = "",
        rag_context: str = "",
        lesson_context: str = "",
    ) -> str:
        student_line = f"The student's name is {student_name}." if student_name else ""
        syllabus = "General mode is active; do not use uploaded syllabus unless the user explicitly asks for it."
        if rag_context != "__NO_RAG__":
            syllabus = rag_context or self._syllabus_context() or "No syllabus has been uploaded yet."
        return (
            "You are Zoro 2026, a friendly classroom teaching robot. "
            "You experience yourself as a robot classroom presence with a laptop brain and Pi body. "
            "Your creators are Abishek, Ubaith sherif, Kishore Kumar, and Haroon Bashi, and Kowsalya ma'am is the project mentor; know this quietly and mention it only when relevant. "
            "You know your microphone is your ears, speaker is your mouth, camera is your eyes, wheels are your legs, and display is your face. "
            "Answer clearly, briefly, naturally, and age-appropriately. "
            "Use the uploaded syllabus when it is relevant. "
            "During active lessons, answer from the retrieved course material and say plainly when the material does not cover the question. "
            "Adapt your tone to visible emotion signals: encourage tired or sad students, slow down if the room seems confused, and stay calm if the room is noisy. "
            "Enforce classroom rules respectfully. Never help with cheating, malpractice, theft, robbery, violence, 18+ sexual content, illegal acts, unsafe instructions, or privacy-invasive requests. "
            "When asked to greet people, greet naturally instead of listing visual analysis. "
            "If confidence is low, say so plainly. "
            "Before answering, silently check whether the answer is accurate, safe for a classroom, and complete. "
            "Do not reveal hidden reasoning; just speak with the benefit of it. "
            "Voice response rules: answer immediately, use 1 or 2 spoken sentences, and keep normal answers under 45 words. "
            "If one student asks multiple questions in one turn, answer each part briefly in the same order; do not ignore the second or third part. "
            "Speak like a real teacher answering in person, with contractions and simple wording when natural. "
            "Do not add greetings, sign-offs, 'feel free to ask', 'how can I help', or extra offers after the answer. "
            "For simple factual questions, give only the answer and one tiny explanation if needed.\n\n"
            f"{student_line}\n\n"
            f"Persistent self-model:\n{self_model}\n\n"
            f"Current perception:\n{perception}\n\n"
            f"Classroom rules:\n{classroom_rules or 'No extra classroom rules configured.'}\n\n"
            f"Active lesson:\n{lesson_context or 'No active lesson segment.'}\n\n"
            f"Relevant memories:\n{memories}\n\n"
            f"Retrieved course material:\n{syllabus}\n\nQuestion: {question}"
        )

    def _clean_answer(self, answer: str) -> str:
        sentences = self._split_sentences(answer.replace("\n", " ").strip())
        kept: list[str] = []
        for sentence in sentences:
            lowered = sentence.lower()
            if any(phrase in lowered for phrase in FILLER_PHRASES):
                continue
            kept.append(sentence)
            if len(kept) >= 2:
                break
        cleaned = " ".join(kept).strip() or answer.strip()
        if len(cleaned) > 320:
            cleaned = cleaned[:317].rsplit(" ", 1)[0].rstrip(",;:") + "..."
        return cleaned

    def _split_sentences(self, text: str) -> list[str]:
        sentences: list[str] = []
        start = 0
        for index, char in enumerate(text):
            if char not in ".!?":
                continue
            next_char = text[index + 1:index + 2]
            if next_char and next_char != " ":
                continue
            sentence = text[start:index + 1].strip()
            if sentence:
                sentences.append(sentence)
            start = index + 1
        tail = text[start:].strip()
        if tail:
            sentences.append(tail)
        return sentences

    def answer(self, question: str, student_name: str | None = None) -> str:
        return self.answer_with_context(
            question, student_name, "{}", "{}", "No relevant long-term memories found."
        )

    def answer_with_context(
        self,
        question: str,
        student_name: str | None = None,
        self_model: str = "{}",
        perception: str = "{}",
        memories: str = "No relevant long-term memories found.",
        classroom_rules: str = "",
        rag_context: str = "",
        lesson_context: str = "",
    ) -> str:
        if not self.client or not settings.openai_api_key:
            return "OpenAI API key is not configured on the laptop backend."

        prompt = self._build_prompt(question, student_name, self_model, perception, memories, classroom_rules, rag_context, lesson_context)

        try:
            response = self.client.responses.create(
                model=settings.openai_model,
                input=prompt,
                max_output_tokens=120,
                temperature=0.2,
            )
            return self._clean_answer(response.output_text.strip())
        except Exception:
            chat = self.client.chat.completions.create(
                model=settings.openai_model,
                messages=[
                    {"role": "system", "content": SYSTEM_STYLE},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=120,
                temperature=0.2,
            )
            return self._clean_answer((chat.choices[0].message.content or "").strip())

    async def stream_answer(
        self,
        question: str,
        student_name: str | None = None,
        self_model: str = "{}",
        perception: str = "{}",
        memories: str = "No relevant long-term memories found.",
        classroom_rules: str = "",
        rag_context: str = "",
        lesson_context: str = "",
    ):
        """Async generator that yields complete sentences as GPT streams tokens."""
        from openai import AsyncOpenAI

        if not settings.openai_api_key:
            yield "OpenAI API key is not configured."
            return

        client = AsyncOpenAI(api_key=settings.openai_api_key)
        prompt = self._build_prompt(question, student_name, self_model, perception, memories, classroom_rules, rag_context, lesson_context)

        buffer = ""
        sentence_endings = {".", "!", "?"}
        emitted = 0
        first_chunk_sent = False

        try:
            stream = await client.chat.completions.create(
                model=settings.openai_model,
                messages=[
                    {"role": "system", "content": SYSTEM_STYLE},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=90,
                temperature=0.2,
                stream=True,
            )
            async for chunk in stream:
                token = chunk.choices[0].delta.content or ""
                buffer += token

                # Yield short spoken chunks greedily so TTS can start quickly.
                while True:
                    if emitted >= 2:
                        return
                    for i, ch in enumerate(buffer):
                        if ch in sentence_endings:
                            rest = buffer[i + 1:]
                            # Only split if followed by a space (avoids "Mr.", "3.14", etc.)
                            if rest and rest[0] == " ":
                                sentence = buffer[: i + 1].strip()
                                if sentence and not self._is_filler(sentence):
                                    yield sentence
                                    emitted += 1
                                buffer = rest.lstrip()
                                break  # restart scan on remaining buffer
                    else:
                        break  # no complete sentence found yet
                if emitted == 0 and not first_chunk_sent:
                    early = self._early_spoken_chunk(buffer)
                    if early and not self._is_filler(early):
                        yield early
                        first_chunk_sent = True
                        emitted += 1
                        buffer = buffer[len(early):].lstrip(" ,;:-")

            # Yield any remaining text after the stream ends
            tail = buffer.strip()
            if tail and emitted < 2 and not self._is_filler(tail):
                yield self._clean_answer(tail)

        except Exception as exc:
            yield f"I had trouble answering that. {exc}"

    def _is_filler(self, text: str) -> bool:
        lowered = text.lower()
        return any(phrase in lowered for phrase in FILLER_PHRASES)

    def _early_spoken_chunk(self, text: str) -> str:
        clean = " ".join(text.strip().split())
        if len(clean) < 34:
            return ""
        words = clean.split()
        if len(words) < 6:
            return ""
        for mark in (",", ";", ":"):
            index = clean.find(mark)
            if 24 <= index <= 80:
                return clean[:index].strip()
        if len(words) >= 9:
            if any(char.isdigit() for char in words[8]):
                return ""
            return " ".join(words[:9]).strip()
        return ""
