import { useEffect, useState } from 'react';
import { API, apiFetch } from '../lib/api';
import { Download, RefreshCw, ShieldAlert, UserCheck } from 'lucide-react';

interface BehaviorStudent {
  student_name: string;
  score: number;
  events: number;
  warnings: number;
  positive: number;
  last_event: string;
  breakdown: Record<string, number>;
}

interface BehaviorEvent {
  id: string;
  student_name: string;
  kind: string;
  note: string;
  score_delta: number;
  severity: string;
  timestamp: string;
}

interface BehaviorSummary {
  students: BehaviorStudent[];
  events: BehaviorEvent[];
  total_events: number;
  generated_at: string;
}

export default function BehaviorPage() {
  const [summary, setSummary] = useState<BehaviorSummary | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => { fetchSummary(); }, []);

  const fetchSummary = async () => {
    setLoading(true);
    const { data } = await apiFetch<BehaviorSummary>(API.behaviorSummary);
    if (data) setSummary(data);
    setLoading(false);
  };

  const students = summary?.students || [];
  const events = summary?.events || [];
  const averageScore = students.length
    ? Math.round(students.reduce((total, student) => total + student.score, 0) / students.length)
    : 100;

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 24 }}>
        <div>
          <h1 style={{ fontSize: 24, fontWeight: 800, color: '#fff' }}>Behavior Report</h1>
          <p style={{ fontSize: 13, color: '#666688', marginTop: 2 }}>
            Respect, attentiveness, rule events, attendance presence, and classroom conduct
          </p>
        </div>
        <div style={{ display: 'flex', gap: 10 }}>
          <a className="zoro-btn zoro-btn-secondary" href={API.behaviorReport}>
            <Download size={14} /> Download CSV
          </a>
          <button className="zoro-btn zoro-btn-secondary" onClick={fetchSummary}>
            <RefreshCw size={14} /> Refresh
          </button>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 14, marginBottom: 20 }}>
        <Metric label="Students Tracked" value={students.length} />
        <Metric label="Average Score" value={averageScore} />
        <Metric label="Behavior Events" value={summary?.total_events || 0} />
      </div>

      {loading ? (
        <div className="zoro-card" style={{ padding: 40, textAlign: 'center', color: '#555577' }}>Loading behavior report...</div>
      ) : students.length === 0 ? (
        <div className="zoro-card" style={{ padding: 40, textAlign: 'center', color: '#555577' }}>
          No behavior events yet. Zoro will mark respectful warnings and classroom rule events here.
        </div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 420px', gap: 20 }}>
          <div className="zoro-card" style={{ overflow: 'hidden' }}>
            <div style={{ padding: 16, borderBottom: '1px solid var(--zoro-border)', fontSize: 14, fontWeight: 700, color: '#dde0f0' }}>
              Student Conduct Scores
            </div>
            <div style={{ display: 'flex', flexDirection: 'column' }}>
              {students.map(student => (
                <div key={student.student_name} style={{ padding: 16, borderBottom: '1px solid var(--zoro-border)' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                    <div style={{
                      width: 34, height: 34, borderRadius: 8,
                      background: student.score >= 80 ? 'rgba(74,222,128,0.12)' : student.score >= 60 ? 'rgba(251,191,36,0.12)' : 'rgba(248,113,113,0.12)',
                      border: '1px solid var(--zoro-border)',
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                    }}>
                      <UserCheck size={16} color={student.score >= 80 ? 'var(--zoro-green)' : student.score >= 60 ? 'var(--zoro-amber)' : 'var(--zoro-red)'} />
                    </div>
                    <div style={{ flex: 1 }}>
                      <div style={{ fontSize: 14, color: '#fff', fontWeight: 700 }}>{student.student_name}</div>
                      <div style={{ fontSize: 11, color: '#666688', marginTop: 3 }}>
                        {student.events} events, {student.warnings} warnings, {student.positive} positives
                      </div>
                    </div>
                    <div style={{ fontSize: 26, fontWeight: 800, color: student.score >= 80 ? 'var(--zoro-green)' : student.score >= 60 ? 'var(--zoro-amber)' : 'var(--zoro-red)' }}>
                      {student.score}
                    </div>
                  </div>
                  <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 12 }}>
                    {Object.entries(student.breakdown).map(([kind, count]) => (
                      <span key={kind} style={{ fontSize: 10, padding: '3px 8px', borderRadius: 4, color: '#aaaacc', background: 'var(--zoro-surface-3)', border: '1px solid var(--zoro-border)' }}>
                        {kind.replace(/_/g, ' ')}: {count}
                      </span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="zoro-card" style={{ padding: 18 }}>
            <h2 style={{ fontSize: 14, color: '#dde0f0', fontWeight: 700, marginBottom: 14, display: 'flex', gap: 8, alignItems: 'center' }}>
              <ShieldAlert size={15} color="var(--zoro-amber)" /> Recent Events
            </h2>
            {events.length === 0 ? (
              <p style={{ fontSize: 12, color: '#555577' }}>No recent events.</p>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                {events.slice(0, 12).map(event => (
                  <div key={event.id} style={{ padding: 10, borderRadius: 8, background: 'var(--zoro-surface-3)', border: '1px solid var(--zoro-border)' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, marginBottom: 5 }}>
                      <span style={{ fontSize: 12, color: '#dde0f0', fontWeight: 700 }}>{event.student_name}</span>
                      <span style={{ fontSize: 11, color: event.score_delta < 0 ? 'var(--zoro-red)' : 'var(--zoro-green)', fontFamily: 'Space Mono, monospace' }}>
                        {event.score_delta > 0 ? '+' : ''}{event.score_delta}
                      </span>
                    </div>
                    <div style={{ fontSize: 11, color: 'var(--zoro-cyan)', fontFamily: 'Space Mono, monospace', marginBottom: 5 }}>
                      {event.kind.replace(/_/g, ' ')} - {event.timestamp?.replace('T', ' ')}
                    </div>
                    <p style={{ margin: 0, fontSize: 12, color: '#888899', lineHeight: 1.5 }}>{event.note}</p>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="zoro-card" style={{ padding: '14px 16px' }}>
      <div style={{ fontSize: 11, color: '#666688', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', fontFamily: 'Space Mono, monospace', marginBottom: 6 }}>{label}</div>
      <div style={{ fontSize: 26, fontWeight: 800, color: '#fff' }}>{value}</div>
    </div>
  );
}
