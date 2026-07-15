import asyncio
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
import math
import time
from typing import Any

import cv2
import numpy as np

from .attendance import AttendanceService
from .behavior import visual_behavior_rule
from .config import settings
from .memory import MemoryStore

try:
    from ultralytics import YOLO
except ImportError:  # pragma: no cover - optional local dependency
    YOLO = None

try:
    from deepface import DeepFace
except ImportError:  # pragma: no cover - optional local dependency
    DeepFace = None


OBSTACLE_CLASSES = {"person", "chair", "bench", "table", "couch", "bed", "dining table", "door"}


@dataclass
class PerceptionState:
    last_frame_at: str | None = None
    frame_width: int | None = None
    frame_height: int | None = None
    objects: list[dict[str, Any]] = field(default_factory=list)
    faces: list[dict[str, Any]] = field(default_factory=list)
    emotions: Counter = field(default_factory=Counter)
    last_events: list[dict[str, Any]] = field(default_factory=list)
    audio_connected: bool = False
    video_connected: bool = False


@dataclass
class MapCell:
    x: int
    y: int
    kind: str = "unknown"
    obstacle_score: float = 0.0
    free_score: float = 0.0
    labels: Counter = field(default_factory=Counter)
    last_seen: str | None = None


@dataclass
class ObjectTrack:
    id: int
    label: str
    box: list[int]
    confidence: float
    obstacle: bool
    first_seen: float
    last_seen: float
    hits: int = 1
    missed: int = 0
    velocity: tuple[float, float] = (0.0, 0.0)
    approach_rate: float = 0.0

    def snapshot(self) -> dict[str, Any]:
        x1, y1, x2, y2 = self.box
        return {
            "id": self.id,
            "label": self.label,
            "confidence": round(self.confidence, 3),
            "box": self.box,
            "obstacle": self.obstacle,
            "center": [round((x1 + x2) / 2), round((y1 + y2) / 2)],
            "velocity": [round(self.velocity[0], 2), round(self.velocity[1], 2)],
            "approach_rate": round(self.approach_rate, 3),
            "age_seconds": round(self.last_seen - self.first_seen, 1),
            "hits": self.hits,
        }


class EnvironmentMap:
    """Small robot-centric classroom map built from monocular camera detections."""

    def __init__(self) -> None:
        self.pose = {"x": 0.0, "y": 0.0, "heading_deg": 0.0}
        self.cells: dict[tuple[int, int], MapCell] = {}
        self.last_path = {
            "status": "unknown",
            "reason": "No camera frame analyzed yet.",
            "updated_at": None,
        }
        self.last_motion = "stop"

    def update_motion(self, direction: str, speed: float = 0.65) -> None:
        self.last_motion = direction
        step = max(0.05, min(speed, 1.0)) * 0.25
        heading = math.radians(self.pose["heading_deg"])
        if direction == "forward":
            self.pose["x"] += math.sin(heading) * step
            self.pose["y"] += math.cos(heading) * step
        elif direction == "backward":
            self.pose["x"] -= math.sin(heading) * step
            self.pose["y"] -= math.cos(heading) * step
        elif direction == "left":
            self.pose["heading_deg"] = (self.pose["heading_deg"] - 12) % 360
        elif direction in {"right", "rotate"}:
            self.pose["heading_deg"] = (self.pose["heading_deg"] + 12) % 360

    def update_from_objects(self, objects: list[dict[str, Any]], width: int, height: int) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        path_blockers: list[str] = []

        for depth in range(1, 5):
            self._mark_free(0, depth, now, 0.08)

        for obj in objects:
            box = obj.get("box") or [0, 0, 0, 0]
            if len(box) != 4:
                continue
            x1, y1, x2, y2 = [float(v) for v in box]
            center_x = (x1 + x2) / 2
            box_h = max(1.0, y2 - y1)
            rel_x = (center_x / max(width, 1)) - 0.5
            bottom = y2 / max(height, 1)
            depth = self._estimate_depth(bottom, box_h / max(height, 1))
            lateral = int(round(rel_x * (2.0 + depth)))
            wx, wy = self._camera_to_world(lateral, depth)

            if obj.get("obstacle"):
                cell = self._cell(wx, wy)
                cell.kind = "obstacle"
                cell.obstacle_score = min(1.0, cell.obstacle_score + float(obj.get("confidence", 0.4)) * 0.35)
                cell.free_score = max(0.0, cell.free_score - 0.15)
                cell.labels[obj.get("label", "object")] += 1
                cell.last_seen = now

                in_walk_corridor = 0.34 <= center_x / max(width, 1) <= 0.66 and bottom >= 0.42
                if in_walk_corridor and float(obj.get("confidence", 0.0)) >= 0.35:
                    path_blockers.append(obj.get("label", "object"))

        if path_blockers:
            self.last_path = {
                "status": "blocked",
                "reason": "Walking path blocked by " + ", ".join(sorted(set(path_blockers))[:3]) + ".",
                "updated_at": now,
            }
        else:
            self.last_path = {
                "status": "clear",
                "reason": "No obstacle detected in the lower center walking corridor.",
                "updated_at": now,
            }

    def _estimate_depth(self, bottom: float, height_ratio: float) -> int:
        if bottom >= 0.82 or height_ratio >= 0.45:
            return 1
        if bottom >= 0.62 or height_ratio >= 0.28:
            return 2
        if bottom >= 0.42:
            return 3
        return 4

    def _camera_to_world(self, lateral: int, depth: int) -> tuple[int, int]:
        heading = math.radians(self.pose["heading_deg"])
        forward_x = math.sin(heading)
        forward_y = math.cos(heading)
        right_x = math.cos(heading)
        right_y = -math.sin(heading)
        world_x = self.pose["x"] + forward_x * depth + right_x * lateral
        world_y = self.pose["y"] + forward_y * depth + right_y * lateral
        return round(world_x), round(world_y)

    def _cell(self, x: int, y: int) -> MapCell:
        key = (x, y)
        if key not in self.cells:
            self.cells[key] = MapCell(x=x, y=y)
        return self.cells[key]

    def _mark_free(self, x: int, y: int, now: str, amount: float) -> None:
        wx, wy = self._camera_to_world(x, y)
        cell = self._cell(wx, wy)
        if cell.obstacle_score < 0.35:
            cell.kind = "walkable"
        cell.free_score = min(1.0, cell.free_score + amount)
        cell.last_seen = now

    def snapshot(self) -> dict[str, Any]:
        cells = sorted(self.cells.values(), key=lambda c: (c.y, c.x))
        mapped = []
        for cell in cells[-80:]:
            label = cell.labels.most_common(1)[0][0] if cell.labels else ""
            mapped.append({
                "x": cell.x,
                "y": cell.y,
                "kind": cell.kind,
                "label": label,
                "obstacle_score": round(cell.obstacle_score, 2),
                "free_score": round(cell.free_score, 2),
                "last_seen": cell.last_seen,
            })
        obstacles = [c for c in cells if c.obstacle_score >= 0.35]
        walkable = [c for c in cells if c.kind == "walkable" and c.free_score >= 0.2]
        return {
            "pose": {k: round(v, 2) for k, v in self.pose.items()},
            "last_motion": self.last_motion,
            "walking_path": self.last_path,
            "obstacle_count": len(obstacles),
            "walkable_count": len(walkable),
            "cells": mapped,
            "summary": self.summary(),
        }

    def summary(self) -> str:
        obstacles = [c for c in self.cells.values() if c.obstacle_score >= 0.35]
        if not obstacles:
            return f"Walking path is {self.last_path['status']}; no stable obstacles mapped yet."
        parts = []
        for cell in sorted(obstacles, key=lambda c: c.obstacle_score, reverse=True)[:6]:
            label = cell.labels.most_common(1)[0][0] if cell.labels else "obstacle"
            parts.append(f"{label} at map cell ({cell.x}, {cell.y})")
        return f"Walking path is {self.last_path['status']}. Mapped: " + "; ".join(parts) + "."


class PerceptionEngine:
    def __init__(self, attendance: AttendanceService, memory: MemoryStore, notifications=None, behavior=None, world_memory=None) -> None:
        self.attendance = attendance
        self.memory = memory
        self.notifications = notifications
        self.behavior = behavior
        self.world_memory = world_memory
        self.state = PerceptionState()
        self.latest_jpeg: bytes | None = None
        self._visible_students: set[str] = set()
        self._event_cooldowns: dict[str, float] = {}
        self.environment_map = EnvironmentMap()
        self._tracks: dict[int, ObjectTrack] = {}
        self._next_track_id = 1
        self._navigation_risk = {
            "level": "unknown",
            "reason": "No camera frame analyzed yet.",
            "recommended_action": "hold",
            "blocked_directions": [],
            "updated_at": None,
        }
        self._yolo = None
        self._behavior_yolo = None
        self._hog = None
        self._face_cascade = None
        if YOLO is not None:
            try:
                self._yolo = YOLO("yolov8n.pt")
            except Exception as exc:
                print(f"Warning: YOLO disabled: {exc}")
        if self._yolo is None:
            try:
                self._hog = cv2.HOGDescriptor()
                self._hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
            except Exception as exc:
                print(f"Warning: OpenCV person detector disabled: {exc}")
        try:
            cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
            self._face_cascade = cv2.CascadeClassifier(cascade_path)
            if self._face_cascade.empty():
                self._face_cascade = None
        except Exception as exc:
                print(f"Warning: OpenCV face detector disabled: {exc}")
        if YOLO is not None and settings.classroom_behavior_model.exists():
            self.load_behavior_model()

    def detector_status(self) -> dict[str, Any]:
        behavior_model = str(settings.classroom_behavior_model)
        if self._yolo is not None:
            return {
                "engine": "yolo",
                "model": "yolov8n.pt",
                "objects": True,
                "emotions": DeepFace is not None,
                "behavior_model": behavior_model,
                "behavior_model_loaded": self._behavior_yolo is not None,
            }
        return {
            "engine": "opencv-fallback",
            "model": "hog+haar",
            "objects": True,
            "emotions": DeepFace is not None,
            "behavior_model": behavior_model,
            "behavior_model_loaded": self._behavior_yolo is not None,
        }

    def load_behavior_model(self) -> dict[str, Any]:
        self._behavior_yolo = None
        if YOLO is None:
            return {"loaded": False, "available": False, "reason": "ultralytics is not installed"}
        if not settings.classroom_behavior_model.exists():
            return {"loaded": False, "available": False, "reason": "model file not found"}
        try:
            self._behavior_yolo = YOLO(str(settings.classroom_behavior_model))
            names = getattr(self._behavior_yolo, "names", None)
            return {
                "loaded": True,
                "available": True,
                "path": str(settings.classroom_behavior_model),
                "classes": names if isinstance(names, dict) else {},
            }
        except Exception as exc:
            self._behavior_yolo = None
            print(f"Warning: classroom behavior YOLO disabled: {exc}")
            return {"loaded": False, "available": True, "path": str(settings.classroom_behavior_model), "reason": str(exc)}

    def accept_frame(self, jpeg: bytes) -> None:
        self.latest_jpeg = jpeg

    async def process_jpeg(self, jpeg: bytes) -> dict[str, Any]:
        self.accept_frame(jpeg)
        array = np.frombuffer(jpeg, dtype=np.uint8)
        frame = cv2.imdecode(array, cv2.IMREAD_COLOR)
        if frame is None:
            return self.snapshot()
        await asyncio.to_thread(self._analyze_frame, frame)
        return self.snapshot()

    def _analyze_frame(self, frame: np.ndarray) -> None:
        height, width = frame.shape[:2]
        self.state.last_frame_at = datetime.now().isoformat(timespec="seconds")
        self.state.frame_width = width
        self.state.frame_height = height

        objects: list[dict[str, Any]] = []
        if self._yolo is not None:
            try:
                result = self._yolo.predict(frame, verbose=False, imgsz=416)[0]
                names = result.names
                for box in result.boxes:
                    cls = int(box.cls[0])
                    label = names.get(cls, str(cls))
                    confidence = float(box.conf[0])
                    x1, y1, x2, y2 = [float(value) for value in box.xyxy[0]]
                    objects.append({
                        "label": label,
                        "confidence": round(confidence, 3),
                        "box": [round(x1), round(y1), round(x2), round(y2)],
                        "obstacle": label in OBSTACLE_CLASSES,
                    })
            except Exception as exc:
                print(f"Warning: YOLO frame analysis failed: {exc}")
        if self._behavior_yolo is not None:
            try:
                result = self._behavior_yolo.predict(frame, verbose=False, imgsz=416)[0]
                names = result.names
                for box in result.boxes:
                    cls = int(box.cls[0])
                    label = names.get(cls, str(cls))
                    confidence = float(box.conf[0])
                    if confidence < 0.35:
                        continue
                    x1, y1, x2, y2 = [float(value) for value in box.xyxy[0]]
                    rule = visual_behavior_rule(label)
                    objects.append({
                        "label": label,
                        "confidence": round(confidence, 3),
                        "box": [round(x1), round(y1), round(x2), round(y2)],
                        "obstacle": False,
                        "behavior": rule["kind"] if rule else "classroom_behavior",
                    })
            except Exception as exc:
                print(f"Warning: classroom behavior analysis failed: {exc}")
        elif self._hog is not None:
            try:
                small = cv2.resize(frame, (min(width, 480), int(height * min(width, 480) / width)))
                scale_x = width / small.shape[1]
                scale_y = height / small.shape[0]
                boxes, weights = self._hog.detectMultiScale(small, winStride=(8, 8), padding=(8, 8), scale=1.05)
                for (x, y, w, h), confidence in zip(boxes, weights):
                    if float(confidence) < 0.35:
                        continue
                    objects.append({
                        "label": "person",
                        "confidence": round(float(confidence), 3),
                        "box": [round(x * scale_x), round(y * scale_y), round((x + w) * scale_x), round((y + h) * scale_y)],
                        "obstacle": True,
                    })
            except Exception as exc:
                print(f"Warning: OpenCV person detection failed: {exc}")
        self.state.objects = objects[:30]
        self._update_tracks(self.state.objects)

        face_result = self.attendance.recognize_and_mark(cv2.imencode(".jpg", frame)[1].tobytes())
        self.state.faces = [{"name": name} for name in face_result.get("marked", [])]
        if self._face_cascade is not None:
            try:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                faces = self._face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(40, 40))
                if faces is not None:
                    for x, y, w, h in faces:
                        self.state.objects.append({
                            "label": "person",
                            "confidence": 0.5,
                            "box": [int(x), int(y), int(x + w), int(y + h)],
                            "obstacle": True,
                        })
            except Exception as exc:
                print(f"Warning: OpenCV face fallback failed: {exc}")
        self.environment_map.update_from_objects(self.state.objects, width, height)
        self._update_navigation_risk(width, height)
        self._update_entry_exit_events({face["name"] for face in self.state.faces})
        self._update_rule_events()
        self._update_visual_behavior_events()
        self._update_world_memory()

        if DeepFace is not None:
            try:
                analyses = DeepFace.analyze(frame, actions=["emotion"], enforce_detection=False, silent=True)
                if isinstance(analyses, dict):
                    analyses = [analyses]
                emotions = [item.get("dominant_emotion") for item in analyses if item.get("dominant_emotion")]
                self.state.emotions = Counter(emotions)
            except Exception as exc:
                print(f"Warning: DeepFace frame analysis failed: {exc}")

        if self.state.faces or self.state.objects:
            self.memory.add(
                "room_observation",
                self.describe_room(),
                {"faces": len(self.state.faces), "objects": len(self.state.objects)},
            )

    def describe_room(self) -> str:
        people = len([obj for obj in self.state.objects if obj["label"] == "person"])
        object_counts = Counter(obj["label"] for obj in self.state.objects)
        object_text = ", ".join(f"{count} {label}" for label, count in object_counts.most_common(6)) or "no classified objects"
        face_text = ", ".join(face["name"] for face in self.state.faces) or "no recognized students"
        emotions = ", ".join(f"{name}: {count}" for name, count in self.state.emotions.items()) or "no emotion signal"
        return (
            f"I can see {people} people, {object_text}. Recognized faces: {face_text}. "
            f"Emotion signals: {emotions}. Internal map: {self.environment_map.summary()}"
        )

    def short_view_answer(self) -> str:
        people = len([obj for obj in self.state.objects if obj["label"] == "person"])
        counts = Counter(obj["label"] for obj in self.state.objects if obj["label"] != "person")
        parts: list[str] = []
        if people == 1:
            parts.append("one person")
        elif people > 1:
            parts.append(f"{people} people")
        for label, count in counts.most_common(3):
            parts.append(f"{count} {label}" if count > 1 else label)
        if not parts:
            return "I do not see anything clearly right now."
        return "I see " + ", ".join(parts) + "."

    def update_motion_estimate(self, direction: str, speed: float = 0.65) -> None:
        self.environment_map.update_motion(direction, speed)

    def _record_event(self, kind: str, message: str, severity: str = "info", metadata: dict[str, Any] | None = None) -> None:
        cooldown_key = f"{kind}:{message}"
        now = time.monotonic()
        if now - self._event_cooldowns.get(cooldown_key, 0.0) < 30:
            return
        self._event_cooldowns[cooldown_key] = now
        event = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "kind": kind,
            "severity": severity,
            "message": message,
            "metadata": metadata or {},
        }
        self.state.last_events = [event, *self.state.last_events[:19]]
        if self.notifications is not None:
            self.notifications.add(kind, message, severity, metadata)

    def _update_entry_exit_events(self, current_faces: set[str]) -> None:
        entered = sorted(current_faces - self._visible_students)
        exited = sorted(self._visible_students - current_faces)
        for name in entered:
            self._record_event("student_entered", f"{name} is visible in the classroom.", "info", {"student_name": name})
            if self.world_memory is not None:
                self.world_memory.observe_human_behavior("entered_classroom", name, "Recognized entering or becoming visible.")
        for name in exited:
            self._record_event(
                "student_left_view",
                f"{name} is no longer visible. Check whether they left the classroom without permission.",
                "warning",
                {"student_name": name},
            )
            if self.behavior is not None:
                self.behavior.add_event(
                    name,
                    "left_without_permission",
                    "No longer visible during classroom monitoring; verify whether the student left without permission.",
                    "warning",
                    {"source": "camera_entry_exit"},
                )
            if self.world_memory is not None:
                self.world_memory.observe_human_behavior("left_view", name, "No longer visible during classroom monitoring.")
        self._visible_students = current_faces

    def _update_rule_events(self) -> None:
        phone_seen = any(obj["label"] in {"cell phone", "mobile phone", "phone"} and obj["confidence"] >= 0.35 for obj in self.state.objects)
        if phone_seen:
            should_mark_behavior = time.monotonic() - self._event_cooldowns.get("behavior:mobile_phone", 0.0) >= 30
            if should_mark_behavior:
                self._event_cooldowns["behavior:mobile_phone"] = time.monotonic()
            self._record_event(
                "classroom_rule",
                "Possible mobile phone use detected during class.",
                "warning",
                {"rule": "no_mobile_phone"},
            )
            if self.behavior is not None and should_mark_behavior:
                current_names = [face.get("name") for face in self.state.faces if face.get("name")]
                for name in current_names or ["Unknown student"]:
                    self.behavior.add_event(
                        name,
                        "mobile_phone",
                        "Possible mobile phone use detected during class.",
                        "warning",
                        {"source": "camera_object_detection"},
                    )

    def _update_visual_behavior_events(self) -> None:
        if self.behavior is None:
            return
        for obj in self.state.objects:
            rule = visual_behavior_rule(obj.get("label", ""))
            if not rule or float(obj.get("confidence", 0.0)) < 0.35:
                continue
            cooldown_key = f"behavior_visual:{rule['kind']}:{obj.get('label')}"
            if time.monotonic() - self._event_cooldowns.get(cooldown_key, 0.0) < 45:
                continue
            self._event_cooldowns[cooldown_key] = time.monotonic()
            names = [face.get("name") for face in self.state.faces if face.get("name")] or ["Unknown student"]
            for name in names:
                self.behavior.add_event(
                    name,
                    rule["kind"],
                    rule["note"],
                    rule["severity"],
                    {"source": "classroom_behavior_model", "label": obj.get("label"), "confidence": obj.get("confidence")},
                    int(rule["score_delta"]),
                )
                if self.world_memory is not None:
                    self.world_memory.observe_human_behavior(rule["kind"], name, rule["note"])

    def _update_world_memory(self) -> None:
        if self.world_memory is None:
            return
        labels = [
            obj.get("label", "")
            for obj in self.state.objects
            if float(obj.get("confidence", 0.0)) >= 0.45
            and obj.get("label") not in {"person"}
        ]
        stable = []
        for label, count in Counter(labels).items():
            if count >= 1:
                cooldown_key = f"world_memory:{label}"
                if time.monotonic() - self._event_cooldowns.get(cooldown_key, 0.0) < 180:
                    continue
                self._event_cooldowns[cooldown_key] = time.monotonic()
                stable.append(label)
        if stable:
            self.world_memory.observe(stable)

    def _update_tracks(self, detections: list[dict[str, Any]]) -> None:
        now = time.monotonic()
        unmatched_tracks = set(self._tracks)
        matched_detections: set[int] = set()

        for index, detection in enumerate(detections):
            box = detection.get("box") or []
            if len(box) != 4:
                continue
            label = detection.get("label", "object")
            best_track_id = None
            best_iou = 0.0
            for track_id in list(unmatched_tracks):
                track = self._tracks[track_id]
                if track.label != label:
                    continue
                iou = self._box_iou(track.box, box)
                if iou > best_iou:
                    best_iou = iou
                    best_track_id = track_id
            if best_track_id is not None and best_iou >= 0.18:
                track = self._tracks[best_track_id]
                old_box = track.box
                old_center = self._center(old_box)
                old_area = self._area(old_box)
                new_center = self._center(box)
                dt = max(0.001, now - track.last_seen)
                new_area = self._area(box)
                track.velocity = ((new_center[0] - old_center[0]) / dt, (new_center[1] - old_center[1]) / dt)
                track.approach_rate = (math.sqrt(new_area) - math.sqrt(old_area)) / dt
                track.box = [int(v) for v in box]
                track.confidence = float(detection.get("confidence", track.confidence))
                track.obstacle = bool(detection.get("obstacle", track.obstacle))
                track.last_seen = now
                track.hits += 1
                track.missed = 0
                detection["track_id"] = track.id
                unmatched_tracks.discard(best_track_id)
                matched_detections.add(index)

        for index, detection in enumerate(detections):
            if index in matched_detections:
                continue
            box = detection.get("box") or []
            if len(box) != 4:
                continue
            track_id = self._next_track_id
            self._next_track_id += 1
            track = ObjectTrack(
                id=track_id,
                label=detection.get("label", "object"),
                box=[int(v) for v in box],
                confidence=float(detection.get("confidence", 0.0)),
                obstacle=bool(detection.get("obstacle", False)),
                first_seen=now,
                last_seen=now,
            )
            self._tracks[track_id] = track
            detection["track_id"] = track_id

        for track_id in list(unmatched_tracks):
            track = self._tracks[track_id]
            track.missed += 1
            if now - track.last_seen > 2.0 or track.missed > 6:
                del self._tracks[track_id]

    def _update_navigation_risk(self, width: int, height: int) -> None:
        now_text = datetime.now().isoformat(timespec="seconds")
        blockers: list[ObjectTrack] = []
        caution: list[ObjectTrack] = []
        blocked_directions: set[str] = set()
        for track in self._tracks.values():
            if not track.obstacle or track.hits < 1:
                continue
            x1, _, x2, y2 = track.box
            center_x = (x1 + x2) / 2
            bottom = y2 / max(height, 1)
            in_front = width * 0.28 <= center_x <= width * 0.72
            close = bottom >= 0.58
            approaching = track.approach_rate > 18 or track.velocity[1] > 70
            if in_front and (close or approaching):
                blockers.append(track)
                blocked_directions.add("forward")
            elif in_front or close:
                caution.append(track)
            if center_x < width * 0.45 and bottom >= 0.5:
                blocked_directions.add("left")
            if center_x > width * 0.55 and bottom >= 0.5:
                blocked_directions.add("right")

        if blockers:
            labels = ", ".join(sorted({track.label for track in blockers})[:3])
            self._navigation_risk = {
                "level": "blocked",
                "reason": f"Collision risk ahead from {labels}.",
                "recommended_action": "stop",
                "blocked_directions": sorted(blocked_directions),
                "updated_at": now_text,
            }
            self._record_event(
                "navigation_risk",
                f"Movement blocked: {labels} in the camera path.",
                "warning",
                {"blocked_directions": sorted(blocked_directions), "track_ids": [track.id for track in blockers]},
            )
        elif caution:
            labels = ", ".join(sorted({track.label for track in caution})[:3])
            self._navigation_risk = {
                "level": "caution",
                "reason": f"Nearby moving or close object detected: {labels}.",
                "recommended_action": "slow",
                "blocked_directions": sorted(blocked_directions),
                "updated_at": now_text,
            }
        else:
            self._navigation_risk = {
                "level": "clear",
                "reason": "Tracked path is clear.",
                "recommended_action": "move_allowed",
                "blocked_directions": [],
                "updated_at": now_text,
            }

    def blockage_for_direction(self, direction: str) -> dict[str, Any] | None:
        width = self.state.frame_width or 1
        danger_zone = {
            "forward": (width * 0.25, width * 0.75),
            "left": (0, width * 0.45),
            "right": (width * 0.55, width),
            "backward": (0, width),
        }.get(direction, (0, width))
        candidates: list[dict[str, Any]] = []
        for obj in self.state.objects:
            if not obj.get("obstacle"):
                continue
            box = obj.get("box") or []
            if len(box) != 4:
                continue
            x1, _, x2, y2 = box
            center = (x1 + x2) / 2
            if danger_zone[0] <= center <= danger_zone[1] and obj["confidence"] >= 0.35:
                candidates.append({**obj, "bottom": y2})
        if not candidates:
            return None
        return max(candidates, key=lambda item: item.get("bottom", 0))

    def clear_directions(self) -> list[str]:
        return [
            direction for direction in ("forward", "left", "right", "backward")
            if self.blockage_for_direction(direction) is None
        ]

    def movement_clear(self, direction: str) -> tuple[bool, str]:
        if direction in set(self._navigation_risk.get("blocked_directions") or []):
            return False, self._navigation_risk.get("reason") or f"Path is not clear for {direction}."
        obj = self.blockage_for_direction(direction)
        if obj is not None:
            return False, f"I see a {obj['label']} in the way, so I should not move {direction}."
        return True, "Path looks clear from the current camera view."

    @staticmethod
    def _center(box: list[float] | list[int]) -> tuple[float, float]:
        x1, y1, x2, y2 = [float(v) for v in box]
        return (x1 + x2) / 2, (y1 + y2) / 2

    @staticmethod
    def _area(box: list[float] | list[int]) -> float:
        x1, y1, x2, y2 = [float(v) for v in box]
        return max(1.0, x2 - x1) * max(1.0, y2 - y1)

    @classmethod
    def _box_iou(cls, a: list[float] | list[int], b: list[float] | list[int]) -> float:
        ax1, ay1, ax2, ay2 = [float(v) for v in a]
        bx1, by1, bx2, by2 = [float(v) for v in b]
        ix1, iy1 = max(ax1, bx1), max(ay1, by1)
        ix2, iy2 = min(ax2, bx2), min(ay2, by2)
        inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
        if inter <= 0:
            return 0.0
        return inter / (cls._area(a) + cls._area(b) - inter)

    def snapshot(self) -> dict[str, Any]:
        return {
            "last_frame_at": self.state.last_frame_at,
            "frame_width": self.state.frame_width,
            "frame_height": self.state.frame_height,
            "objects": self.state.objects,
            "faces": self.state.faces,
            "emotions": dict(self.state.emotions),
            "last_events": self.state.last_events,
            "audio_connected": self.state.audio_connected,
            "video_connected": self.state.video_connected,
            "detector": self.detector_status(),
            "environment_map": self.environment_map.snapshot(),
            "tracks": [track.snapshot() for track in sorted(self._tracks.values(), key=lambda item: item.id)],
            "navigation_risk": self._navigation_risk,
        }

    def context(self) -> str:
        return json.dumps(self.snapshot(), ensure_ascii=True, indent=2)
