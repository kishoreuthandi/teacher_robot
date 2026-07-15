import { useEffect, useState } from 'react';
import { API, apiFetch } from '../lib/api';
import { AlertTriangle, Bell, BookOpen, Brain, CheckCircle, Map, RefreshCw, Shield, Timer, Zap } from 'lucide-react';

interface NotificationItem {
  id: string;
  time: string;
  kind: string;
  severity: 'info' | 'warning' | 'error';
  message: string;
}

interface LessonPlan {
  subject: string;
  duration_minutes: number;
  break_count: number;
  source_files: string[];
  topics: string[];
  schedule: Array<{ type: string; minutes: number; title: string; activity: string; topics?: string[] }>;
  rules: string[];
}

interface ClassroomState {
  mode: 'general' | 'teaching';
  teaching_active: boolean;
  strict_mode: boolean;
  permitted_exits: Array<{ student_name: string; reason: string; time: string }>;
  rules: string[];
  perception: {
    faces: Array<{ name: string }>;
    emotions: Record<string, number>;
    objects: Array<{ label: string; confidence: number; obstacle?: boolean }>;
    audio_connected: boolean;
    video_connected: boolean;
    detector?: { engine: string; model: string; objects: boolean; emotions: boolean };
    environment_map?: EnvironmentMap;
    tracks?: ObjectTrack[];
    navigation_risk?: NavigationRisk;
  };
  notifications: NotificationItem[];
  voice?: {
    last_partial?: string;
    last_final?: string;
    transcript_source?: string;
    starts_speaking_after_transcript_ms?: number;
    starts_speaking_after_first_audio_ms?: number;
  };
}

interface EnvironmentMap {
  pose: { x: number; y: number; heading_deg: number };
  walking_path: { status: string; reason: string; updated_at?: string };
  obstacle_count: number;
  walkable_count: number;
  summary: string;
  cells: Array<{ x: number; y: number; kind: string; label: string; obstacle_score: number; free_score: number }>;
}

interface ObjectTrack {
  id: number;
  label: string;
  confidence: number;
  obstacle?: boolean;
  center: [number, number];
  velocity: [number, number];
  approach_rate: number;
  age_seconds: number;
  hits: number;
}

interface NavigationRisk {
  level: 'unknown' | 'clear' | 'caution' | 'blocked';
  reason: string;
  recommended_action: string;
  blocked_directions: string[];
  updated_at?: string;
}

interface VoiceLatency {
  latest_live_turn?: {
    first_tts_ready_ms?: number;
    first_speaker_done_ms?: number;
    total_turn_ms?: number;
    transcript?: string;
    updated_at?: string;
  };
}

interface LessonProgress {
  subject: string;
  status: string;
  current_index: number;
  current_item?: { type: string; minutes: number; title: string; activity: string };
  completed_minutes: number;
  remaining_minutes: number;
  schedule_count: number;
}

export default function ClassroomPage() {
  const [state, setState] = useState<ClassroomState | null>(null);
  const [latency, setLatency] = useState<VoiceLatency | null>(null);
  const [benchmarking, setBenchmarking] = useState(false);
  const [plan, setPlan] = useState<LessonPlan | null>(null);
  const [progress, setProgress] = useState<LessonProgress | null>(null);
  const [subject, setSubject] = useState('');
  const [duration, setDuration] = useState(30);
  const [breakCount, setBreakCount] = useState(2);
  const [modeBusy, setModeBusy] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchState();
    const interval = setInterval(fetchState, 1500);
    return () => clearInterval(interval);
  }, []);

  const fetchState = async () => {
    const { data } = await apiFetch<ClassroomState>(API.classroomState);
    if (data) setState(data);
    const latencyResult = await apiFetch<VoiceLatency>(API.voiceLatency);
    if (latencyResult.data) setLatency(latencyResult.data);
    const progressResult = await apiFetch<{ items: LessonProgress[] }>(API.lessonProgress);
    if (progressResult.data) setProgress(progressResult.data.items.find(item => item.status === 'active') || progressResult.data.items[0] || null);
    setLoading(false);
  };

  const runLatencyBenchmark = async () => {
    setBenchmarking(true);
    const { data } = await apiFetch<any>(API.voiceLatencyBenchmark, { method: 'POST' });
    if (data) {
      setLatency({
        latest_live_turn: {
          first_tts_ready_ms: data.tts_full_response_ms,
          first_speaker_done_ms: data.streaming_tts_to_speaker?.total_stream_ms,
          total_turn_ms: data.total_benchmark_ms,
          transcript: data.transcript,
          updated_at: new Date().toISOString().slice(0, 19),
        },
      });
    }
    setBenchmarking(false);
  };

  const buildPlan = async (start = false) => {
    const { data } = await apiFetch<LessonPlan>(start ? API.lessonStart : API.lessonPlan, {
      method: 'POST',
      body: JSON.stringify({ subject, duration_minutes: duration, break_count: breakCount }),
    });
    if (data) {
      setPlan(data);
      fetchState();
    }
  };

  const stopLesson = async () => {
    await apiFetch(API.lessonStop, { method: 'POST' });
    fetchState();
  };

  const setTeachingMode = async (teaching: boolean) => {
    setModeBusy(true);
    await apiFetch(API.classroomMode, {
      method: 'PUT',
      body: JSON.stringify({ teaching }),
    });
    await fetchState();
    setModeBusy(false);
  };

  const advanceLesson = async () => {
    await apiFetch(API.lessonAdvance, {
      method: 'POST',
      body: JSON.stringify({ subject: progress?.subject || subject || 'General' }),
    });
    fetchState();
  };

  const notifications = state?.notifications || [];
  const faces = state?.perception.faces.map(f => f.name).join(', ') || 'No recognized students';
  const emotions = Object.entries(state?.perception.emotions || {}).map(([k, v]) => `${k}: ${v}`).join(', ') || 'No emotion signal';
  const map = state?.perception.environment_map;
  const detector = state?.perception.detector;
  const risk = state?.perception.navigation_risk;
  const tracks = state?.perception.tracks || [];
  const voiceLatency = latency?.latest_live_turn || {};

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 24 }}>
        <div>
          <h1 style={{ fontSize: 24, fontWeight: 800, color: '#fff' }}>Classroom Brain</h1>
          <p style={{ fontSize: 13, color: '#666688', marginTop: 2 }}>
            Awareness, rules, permissions, lesson planning, and faculty alerts
          </p>
        </div>
        <button className="zoro-btn zoro-btn-secondary" onClick={fetchState}>
          <RefreshCw size={14} /> Refresh
        </button>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 14, marginBottom: 20 }}>
        <ModeToggleCard
          active={!!state?.teaching_active}
          busy={modeBusy}
          onChange={setTeachingMode}
        />
        <StatusCard icon={Shield} label="Strict Rules" value={state?.strict_mode ? 'Enabled' : 'Disabled'} active={!!state?.strict_mode} />
        <StatusCard icon={Bell} label="Alerts" value={String(notifications.length)} active={notifications.some(n => n.severity === 'warning')} />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '360px 1fr', gap: 20 }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div className="zoro-card" style={{ padding: 18 }}>
            <h2 style={{ fontSize: 14, color: '#dde0f0', fontWeight: 700, marginBottom: 14, display: 'flex', gap: 8, alignItems: 'center' }}>
              <BookOpen size={15} color="var(--zoro-cyan)" /> Lesson Module
            </h2>
            <p style={{ fontSize: 12, color: '#888899', lineHeight: 1.6, marginBottom: 12 }}>
              Enter the subject, total class minutes, and number of breaks. Example: 30 minutes with 2 breaks creates three teaching parts with two 5-minute pauses.
            </p>
            <label style={{ fontSize: 11, color: '#666688', fontFamily: 'Space Mono, monospace' }}>SUBJECT / SYLLABUS</label>
            <input className="zoro-input" placeholder="Subject or file name" value={subject} onChange={e => setSubject(e.target.value)} style={{ marginBottom: 10 }} />
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginBottom: 12 }}>
              <div>
                <label style={{ fontSize: 11, color: '#666688', fontFamily: 'Space Mono, monospace' }}>MINUTES</label>
                <input className="zoro-input" type="number" min={10} max={90} value={duration} onChange={e => setDuration(Number(e.target.value))} />
              </div>
              <div>
                <label style={{ fontSize: 11, color: '#666688', fontFamily: 'Space Mono, monospace' }}>BREAKS</label>
                <input className="zoro-input" type="number" min={0} max={4} value={breakCount} onChange={e => setBreakCount(Number(e.target.value))} />
              </div>
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <button className="zoro-btn zoro-btn-secondary" onClick={() => buildPlan(false)}>Build Plan</button>
              <button className="zoro-btn zoro-btn-primary" onClick={() => buildPlan(true)}>Start</button>
              <button className="zoro-btn zoro-btn-danger" onClick={stopLesson}>Stop</button>
            </div>
            {progress && (
              <div style={{ marginTop: 14, padding: 12, borderRadius: 8, background: 'var(--zoro-surface-3)', border: '1px solid var(--zoro-border)' }}>
                <InfoLine label="Resume subject" value={progress.subject} />
                <InfoLine label="Current part" value={progress.current_item?.title || 'Completed'} />
                <InfoLine label="Done / left" value={`${progress.completed_minutes} min / ${progress.remaining_minutes} min`} />
                <button className="zoro-btn zoro-btn-secondary" onClick={advanceLesson} style={{ width: '100%', marginTop: 8 }}>
                  Mark Current Part Done
                </button>
              </div>
            )}
          </div>

          <div className="zoro-card" style={{ padding: 18 }}>
            <h2 style={{ fontSize: 14, color: '#dde0f0', fontWeight: 700, marginBottom: 12 }}>Current Awareness</h2>
            <InfoLine label="Students" value={faces} />
            <InfoLine label="Emotions" value={emotions} />
            <InfoLine label="Detector" value={detector ? `${detector.engine} · ${detector.model}` : '-'} />
            <InfoLine label="Audio" value={state?.perception.audio_connected ? 'Connected' : 'Disconnected'} />
            <InfoLine label="Video" value={state?.perception.video_connected ? 'Connected' : 'Disconnected'} />
          </div>

          <div className="zoro-card" style={{ padding: 18 }}>
            <h2 style={{ fontSize: 14, color: '#dde0f0', fontWeight: 700, marginBottom: 12, display: 'flex', gap: 8, alignItems: 'center' }}>
              <Shield size={15} color={risk?.level === 'blocked' ? 'var(--zoro-red)' : risk?.level === 'caution' ? 'var(--zoro-amber)' : 'var(--zoro-green)'} /> Navigation Safety
            </h2>
            <InfoLine label="Risk" value={risk?.level || 'Unknown'} />
            <InfoLine label="Action" value={risk?.recommended_action || 'Hold'} />
            <InfoLine label="Blocked" value={risk?.blocked_directions?.length ? risk.blocked_directions.join(', ') : 'None'} />
            <p style={{ fontSize: 12, color: '#888899', lineHeight: 1.5, marginTop: 8 }}>{risk?.reason || 'No camera risk signal yet.'}</p>
          </div>

          <div className="zoro-card" style={{ padding: 18 }}>
            <h2 style={{ fontSize: 14, color: '#dde0f0', fontWeight: 700, marginBottom: 12, display: 'flex', gap: 8, alignItems: 'center' }}>
              <Map size={15} color="var(--zoro-cyan)" /> Environment Map
            </h2>
            <InfoLine label="Walking path" value={map?.walking_path?.status || 'Unknown'} />
            <InfoLine label="Obstacles" value={String(map?.obstacle_count ?? 0)} />
            <InfoLine label="Walkable cells" value={String(map?.walkable_count ?? 0)} />
            <InfoLine label="Pose" value={map ? `x ${map.pose.x}, y ${map.pose.y}, ${map.pose.heading_deg}°` : '-'} />
            <p style={{ fontSize: 12, color: '#888899', lineHeight: 1.5, marginTop: 8 }}>{map?.summary || 'No map yet.'}</p>
          </div>

          <div className="zoro-card" style={{ padding: 18 }}>
            <h2 style={{ fontSize: 14, color: '#dde0f0', fontWeight: 700, marginBottom: 12 }}>Tracked Objects</h2>
            {tracks.length === 0 ? (
              <p style={{ fontSize: 12, color: '#555577' }}>No stable tracks yet.</p>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {tracks.slice(0, 6).map(track => (
                  <div key={track.id} style={{ display: 'grid', gridTemplateColumns: '48px 1fr', gap: 8, padding: 9, borderRadius: 8, background: 'var(--zoro-surface-3)', border: '1px solid var(--zoro-border)' }}>
                    <div style={{ color: track.obstacle ? 'var(--zoro-amber)' : 'var(--zoro-cyan)', fontFamily: 'Space Mono, monospace', fontSize: 12 }}>#{track.id}</div>
                    <div>
                      <div style={{ color: '#dde0f0', fontSize: 12, fontWeight: 700 }}>{track.label} Â· {Math.round(track.confidence * 100)}%</div>
                      <div style={{ color: '#666688', fontSize: 11, marginTop: 3 }}>
                        vx {Math.round(track.velocity[0])}, vy {Math.round(track.velocity[1])} Â· approach {track.approach_rate.toFixed(1)}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="zoro-card" style={{ padding: 18 }}>
            <h2 style={{ fontSize: 14, color: '#dde0f0', fontWeight: 700, marginBottom: 12, display: 'flex', gap: 8, alignItems: 'center' }}>
              <Zap size={15} color="var(--zoro-amber)" /> Voice Latency
            </h2>
            <InfoLine label="First TTS" value={voiceLatency.first_tts_ready_ms ? `${voiceLatency.first_tts_ready_ms} ms` : 'No live turn yet'} />
            <InfoLine label="First audio" value={state?.voice?.starts_speaking_after_transcript_ms ? `${state.voice.starts_speaking_after_transcript_ms} ms after text` : '-'} />
            <InfoLine label="Heard text" value={state?.voice?.last_partial || state?.voice?.last_final || 'Listening...'} />
            <InfoLine label="Source" value={state?.voice?.transcript_source || '-'} />
            <InfoLine label="Speaker done" value={voiceLatency.first_speaker_done_ms ? `${voiceLatency.first_speaker_done_ms} ms` : '-'} />
            <InfoLine label="Total turn" value={voiceLatency.total_turn_ms ? `${voiceLatency.total_turn_ms} ms` : '-'} />
            <button className="zoro-btn zoro-btn-secondary" onClick={runLatencyBenchmark} disabled={benchmarking} style={{ width: '100%', marginTop: 8 }}>
              {benchmarking ? 'Testing...' : 'Run Benchmark'}
            </button>
          </div>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div className="zoro-card" style={{ padding: 18 }}>
            <h2 style={{ fontSize: 14, color: '#dde0f0', fontWeight: 700, marginBottom: 12, display: 'flex', gap: 8, alignItems: 'center' }}>
              <AlertTriangle size={15} color="var(--zoro-amber)" /> Faculty Notifications
            </h2>
            {loading ? (
              <p style={{ color: '#555577' }}>Loading...</p>
            ) : notifications.length === 0 ? (
              <p style={{ color: '#555577' }}>No alerts yet.</p>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {notifications.map(item => (
                  <div key={item.id} style={{
                    padding: 12, borderRadius: 8,
                    background: item.severity === 'warning' ? 'rgba(251,191,36,0.08)' : 'var(--zoro-surface-3)',
                    border: `1px solid ${item.severity === 'warning' ? 'rgba(251,191,36,0.25)' : 'var(--zoro-border)'}`,
                  }}>
                    <div style={{ fontSize: 12, color: item.severity === 'warning' ? 'var(--zoro-amber)' : 'var(--zoro-cyan)', fontFamily: 'Space Mono, monospace' }}>
                      {item.kind} · {item.time}
                    </div>
                    <div style={{ fontSize: 13, color: '#dde0f0', marginTop: 4 }}>{item.message}</div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {plan && (
            <div className="zoro-card" style={{ padding: 18 }}>
              <h2 style={{ fontSize: 14, color: '#dde0f0', fontWeight: 700, marginBottom: 12, display: 'flex', gap: 8, alignItems: 'center' }}>
                <Timer size={15} color="var(--zoro-cyan)" /> {plan.subject} · {plan.duration_minutes} min
              </h2>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {plan.schedule.map((item, index) => (
                  <div key={index} style={{ padding: 10, borderRadius: 8, background: 'var(--zoro-surface-3)', border: '1px solid var(--zoro-border)' }}>
                    <div style={{ color: item.type === 'break' ? 'var(--zoro-amber)' : 'var(--zoro-cyan)', fontSize: 12, fontWeight: 700 }}>
                      {item.minutes} min · {item.title}
                    </div>
                    <div style={{ color: '#888899', fontSize: 12, marginTop: 4 }}>{item.activity}</div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function StatusCard({ icon: Icon, label, value, active }: { icon: any; label: string; value: string; active: boolean }) {
  return (
    <div className="zoro-card" style={{ padding: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <Icon size={15} color={active ? 'var(--zoro-green)' : '#555577'} />
        <span style={{ fontSize: 11, color: '#666688', textTransform: 'uppercase', fontFamily: 'Space Mono, monospace' }}>{label}</span>
      </div>
      <div style={{ fontSize: 22, fontWeight: 800, color: active ? 'var(--zoro-green)' : '#dde0f0' }}>{value}</div>
    </div>
  );
}

function ModeToggleCard({ active, busy, onChange }: { active: boolean; busy: boolean; onChange: (teaching: boolean) => void }) {
  return (
    <div className="zoro-card" style={{ padding: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
            <Brain size={15} color={active ? 'var(--zoro-green)' : 'var(--zoro-cyan)'} />
            <span style={{ fontSize: 11, color: '#666688', textTransform: 'uppercase', fontFamily: 'Space Mono, monospace' }}>Mode</span>
          </div>
          <div style={{ fontSize: 22, fontWeight: 800, color: active ? 'var(--zoro-green)' : '#dde0f0' }}>
            {active ? 'Teaching' : 'General'}
          </div>
        </div>
        <button
          type="button"
          aria-label="Toggle teaching mode"
          disabled={busy}
          onClick={() => onChange(!active)}
          style={{
            width: 58,
            height: 32,
            borderRadius: 999,
            border: `1px solid ${active ? 'rgba(74,222,128,0.45)' : 'var(--zoro-border)'}`,
            background: active ? 'rgba(74,222,128,0.18)' : 'var(--zoro-surface-3)',
            padding: 3,
            cursor: busy ? 'wait' : 'pointer',
            display: 'flex',
            alignItems: 'center',
            justifyContent: active ? 'flex-end' : 'flex-start',
          }}
        >
          <span
            style={{
              width: 24,
              height: 24,
              borderRadius: '50%',
              background: active ? 'var(--zoro-green)' : '#666688',
              boxShadow: active ? '0 0 12px rgba(74,222,128,0.45)' : 'none',
              display: 'block',
            }}
          />
        </button>
      </div>
      <div style={{ marginTop: 8, fontSize: 11, lineHeight: 1.45, color: '#777799' }}>
        {active ? 'Syllabus/RAG only' : 'Broad general answers'}
      </div>
    </div>
  );
}

function InfoLine({ label, value }: { label: string; value?: string }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, marginBottom: 10 }}>
      <span style={{ fontSize: 12, color: '#888899' }}>{label}</span>
      <span style={{ fontSize: 12, color: '#dde0f0', textAlign: 'right' }}>{value || '-'}</span>
    </div>
  );
}
