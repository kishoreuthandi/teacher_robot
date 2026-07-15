// Central config for Zoro robot API endpoints
// Pi runs on port 8000 (direct) | Laptop backend on port 8001 (ngrok tunnel)

const ROBOT_BASE =
  localStorage.getItem('zoro_robot_url') ||
  import.meta.env.VITE_ROBOT_URL ||
  'http://127.0.0.1:8001';
const ROBOT_WS_BASE = ROBOT_BASE.replace(/^http/i, 'ws');

export const API = {
  base: ROBOT_BASE,

  // --- Robot direct endpoints (via laptop backend proxy) ---
  videoStream: `${ROBOT_BASE}/robot/video.mjpeg`,
  snapshot: `${ROBOT_BASE}/robot/snapshot.jpg`,

  // Motor control
  motorMove: `${ROBOT_BASE}/robot/move`,
  motorStop: `${ROBOT_BASE}/robot/stop`,
  motorSocket: `${ROBOT_WS_BASE}/ws/robot-control`,

  // Attendance
  attendanceUpload: `${ROBOT_BASE}/attendance/upload`,
  attendanceLogs: `${ROBOT_BASE}/attendance/logs`,
  attendanceDownload: (filename: string) => `${ROBOT_BASE}/attendance/download/${filename}`,
  attendanceAutoScan: `${ROBOT_BASE}/attendance/auto-scan`,
  attendanceAutoScanRunNow: `${ROBOT_BASE}/attendance/auto-scan/run-now`,

  // Syllabus / AI training
  syllabusUpload: `${ROBOT_BASE}/syllabus/upload`,
  syllabusList: `${ROBOT_BASE}/syllabus/list`,
  syllabusDelete: (id: string) => `${ROBOT_BASE}/syllabus/${id}`,
  ragStatus: `${ROBOT_BASE}/rag/status`,
  ragReindex: `${ROBOT_BASE}/rag/reindex`,
  ragSearch: (q: string, subject = '') => `${ROBOT_BASE}/rag/search?q=${encodeURIComponent(q)}&subject=${encodeURIComponent(subject)}`,
  assessmentList: `${ROBOT_BASE}/assessments`,
  assessmentCreate: `${ROBOT_BASE}/assessments`,
  assessmentSubmit: (id: string) => `${ROBOT_BASE}/assessments/${id}/submit`,
  assessmentClose: (id: string) => `${ROBOT_BASE}/assessments/${id}/close`,
  worldSummary: `${ROBOT_BASE}/world/summary`,
  worldMemory: `${ROBOT_BASE}/world/memory`,
  worldTeach: `${ROBOT_BASE}/world/teach`,

  // Special speech / announcements
  speechList: `${ROBOT_BASE}/speech/list`,
  speechCreate: `${ROBOT_BASE}/speech/create`,
  speechUpdate: (id: string) => `${ROBOT_BASE}/speech/${id}`,
  speechTrigger: (id: string) => `${ROBOT_BASE}/speech/trigger/${id}`,
  speechDelete: (id: string) => `${ROBOT_BASE}/speech/${id}`,

  // AI Transcriptions
  transcriptList: `${ROBOT_BASE}/transcripts/list`,
  transcriptSession: (id: string) => `${ROBOT_BASE}/transcripts/session/${id}`,

  // Classroom brain
  classroomState: `${ROBOT_BASE}/classroom/state`,
  classroomMode: `${ROBOT_BASE}/classroom/mode`,
  environmentMap: `${ROBOT_BASE}/environment/map`,
  voiceLatency: `${ROBOT_BASE}/voice/latency`,
  voiceLatencyBenchmark: `${ROBOT_BASE}/voice/latency/benchmark`,
  voiceConfig: `${ROBOT_BASE}/voice/config`,
  notifications: `${ROBOT_BASE}/notifications`,
  behaviorSummary: `${ROBOT_BASE}/behavior/summary`,
  behaviorReport: `${ROBOT_BASE}/behavior/report.csv`,
  behaviorModelStatus: `${ROBOT_BASE}/behavior/model/status`,
  behaviorModelUpload: `${ROBOT_BASE}/behavior/model/upload`,
  behaviorModelReload: `${ROBOT_BASE}/behavior/model/reload`,
  behaviorModelDownloadKaggle: `${ROBOT_BASE}/behavior/model/download-kaggle`,
  peopleProfiles: `${ROBOT_BASE}/people/profiles`,
  peopleIntroduce: `${ROBOT_BASE}/people/introduce`,
  lessonPlan: `${ROBOT_BASE}/lesson/plan`,
  lessonStart: `${ROBOT_BASE}/lesson/start`,
  lessonStop: `${ROBOT_BASE}/lesson/stop`,
  lessonProgress: `${ROBOT_BASE}/lesson/progress`,
  lessonAdvance: `${ROBOT_BASE}/lesson/advance`,

  // Robot status
  status: `${ROBOT_BASE}/status`,

  // Voice control
  voiceStart: `${ROBOT_BASE}/voice/start`,
  voiceStop: `${ROBOT_BASE}/voice/stop`,
};

export async function apiFetch<T>(
  url: string,
  options?: RequestInit
): Promise<{ data: T | null; error: string | null }> {
  try {
    const res = await fetch(url, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...options?.headers,
      },
    });

    if (!res.ok) {
      const text = await res.text();
      return { data: null, error: `HTTP ${res.status}: ${text}` };
    }

    const data = await res.json();
    return { data, error: null };
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : 'Network error';
    return { data: null, error: message };
  }
}
