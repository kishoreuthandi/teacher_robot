import { useEffect, useState } from 'react';
import { API, apiFetch } from '../lib/api';
import { CheckCircle, FileCheck, Plus, RefreshCw, XCircle } from 'lucide-react';

interface Assessment {
  id: string;
  title: string;
  subject: string;
  instructions: string;
  due_at?: string;
  created_at: string;
  status: string;
  submissions: Record<string, { student_name: string; submitted_at: string; status: string; note?: string }>;
  missing_students?: string[];
}

export default function AssessmentsPage() {
  const [items, setItems] = useState<Assessment[]>([]);
  const [title, setTitle] = useState('');
  const [subject, setSubject] = useState('');
  const [instructions, setInstructions] = useState('');
  const [studentName, setStudentName] = useState('');
  const [loading, setLoading] = useState(true);

  useEffect(() => { fetchItems(); }, []);

  const fetchItems = async () => {
    setLoading(true);
    const { data } = await apiFetch<{ items: Assessment[] }>(API.assessmentList);
    if (data) setItems(data.items);
    setLoading(false);
  };

  const createAssessment = async () => {
    const { data } = await apiFetch<Assessment>(API.assessmentCreate, {
      method: 'POST',
      body: JSON.stringify({ title, subject, instructions }),
    });
    if (data) {
      setTitle('');
      setSubject('');
      setInstructions('');
      fetchItems();
    }
  };

  const submit = async (id: string) => {
    await apiFetch(API.assessmentSubmit(id), {
      method: 'POST',
      body: JSON.stringify({ student_name: studentName }),
    });
    fetchItems();
  };

  const close = async (id: string) => {
    await apiFetch(API.assessmentClose(id), {
      method: 'POST',
      body: JSON.stringify({}),
    });
    fetchItems();
  };

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 24 }}>
        <div>
          <h1 style={{ fontSize: 24, fontWeight: 800, color: '#fff' }}>Assessments</h1>
          <p style={{ fontSize: 13, color: '#666688', marginTop: 2 }}>
            Track class work completion and mark missed assessments in behavior reports
          </p>
        </div>
        <button className="zoro-btn zoro-btn-secondary" onClick={fetchItems}>
          <RefreshCw size={14} /> Refresh
        </button>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '360px 1fr', gap: 20 }}>
        <div className="zoro-card" style={{ padding: 18, alignSelf: 'start' }}>
          <h2 style={{ fontSize: 14, color: '#dde0f0', fontWeight: 700, marginBottom: 14, display: 'flex', gap: 8, alignItems: 'center' }}>
            <Plus size={15} color="var(--zoro-cyan)" /> New Assessment
          </h2>
          <label style={{ fontSize: 11, color: '#666688', fontFamily: 'Space Mono, monospace' }}>TITLE</label>
          <input className="zoro-input" value={title} onChange={e => setTitle(e.target.value)} placeholder="Quiz 1, worksheet, activity..." style={{ marginBottom: 10 }} />
          <label style={{ fontSize: 11, color: '#666688', fontFamily: 'Space Mono, monospace' }}>SUBJECT</label>
          <input className="zoro-input" value={subject} onChange={e => setSubject(e.target.value)} placeholder="Subject" style={{ marginBottom: 10 }} />
          <label style={{ fontSize: 11, color: '#666688', fontFamily: 'Space Mono, monospace' }}>INSTRUCTIONS</label>
          <textarea className="zoro-input" value={instructions} onChange={e => setInstructions(e.target.value)} rows={4} style={{ marginBottom: 12, resize: 'vertical' }} />
          <button className="zoro-btn zoro-btn-primary" onClick={createAssessment} style={{ width: '100%' }}>
            <FileCheck size={14} /> Create
          </button>

          <div style={{ height: 1, background: 'var(--zoro-border)', margin: '18px 0' }} />
          <label style={{ fontSize: 11, color: '#666688', fontFamily: 'Space Mono, monospace' }}>STUDENT NAME FOR COMPLETION</label>
          <input className="zoro-input" value={studentName} onChange={e => setStudentName(e.target.value)} placeholder="Recognized name or manual name" />
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {loading ? (
            <div className="zoro-card" style={{ padding: 40, textAlign: 'center', color: '#555577' }}>Loading assessments...</div>
          ) : items.length === 0 ? (
            <div className="zoro-card" style={{ padding: 40, textAlign: 'center', color: '#555577' }}>No assessments yet.</div>
          ) : items.map(item => {
            const submissions = Object.values(item.submissions || {});
            return (
              <div key={item.id} className="zoro-card" style={{ padding: 16 }}>
                <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12 }}>
                  <div>
                    <div style={{ fontSize: 15, color: '#fff', fontWeight: 800 }}>{item.title}</div>
                    <div style={{ fontSize: 12, color: 'var(--zoro-cyan)', marginTop: 4 }}>{item.subject}</div>
                    {item.instructions && <p style={{ fontSize: 12, color: '#888899', lineHeight: 1.5, margin: '8px 0 0' }}>{item.instructions}</p>}
                  </div>
                  <span style={{ fontSize: 11, color: item.status === 'open' ? 'var(--zoro-green)' : 'var(--zoro-amber)', fontFamily: 'Space Mono, monospace' }}>
                    {item.status.toUpperCase()}
                  </span>
                </div>
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 14 }}>
                  <button className="zoro-btn zoro-btn-secondary" onClick={() => submit(item.id)} disabled={!studentName}>
                    <CheckCircle size={14} /> Mark Completed
                  </button>
                  <button className="zoro-btn zoro-btn-danger" onClick={() => close(item.id)}>
                    <XCircle size={14} /> Close & Mark Missing
                  </button>
                </div>
                <div style={{ marginTop: 12, fontSize: 12, color: '#888899' }}>
                  Completed: {submissions.length ? submissions.map(s => s.student_name).join(', ') : 'None yet'}
                </div>
                {item.missing_students && item.missing_students.length > 0 && (
                  <div style={{ marginTop: 8, fontSize: 12, color: 'var(--zoro-red)' }}>
                    Missing: {item.missing_students.join(', ')}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
