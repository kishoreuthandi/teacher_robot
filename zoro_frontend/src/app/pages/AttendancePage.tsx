import { useState, useEffect, useRef } from 'react';
import { API, apiFetch } from '../lib/api';
import { Upload, Download, RefreshCw, Users, CheckCircle, XCircle, Clock, FileText } from 'lucide-react';

interface AttendanceLog {
  id: string;
  filename: string;
  date: string;
  total_students: number;
  present: number;
  absent: number;
}

interface AttendanceRecord {
  name: string;
  status: 'present' | 'absent' | 'unknown';
  time?: string;
  confidence?: number;
}

export default function AttendancePage() {
  const [logs, setLogs] = useState<AttendanceLog[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [selectedLog, setSelectedLog] = useState<AttendanceLog | null>(null);
  const [records, setRecords] = useState<AttendanceRecord[]>([]);
  const [loadingRecords, setLoadingRecords] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [uploadMsg, setUploadMsg] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => { fetchLogs(); }, []);

  const fetchLogs = async () => {
    setLoading(true);
    const { data } = await apiFetch<AttendanceLog[]>(API.attendanceLogs);
    if (data) setLogs(data);
    setLoading(false);
  };

  const fetchRecords = async (log: AttendanceLog) => {
    setSelectedLog(log);
    setLoadingRecords(true);
    const { data } = await apiFetch<AttendanceRecord[]>(
      `${API.attendanceLogs}/${log.id}/records`
    );
    if (data) setRecords(data);
    setLoadingRecords(false);
  };

  const handleUpload = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    setUploading(true);
    setUploadMsg(null);

    const formData = new FormData();
    Array.from(files).forEach(f => formData.append('files', f));

    try {
      const res = await fetch(API.attendanceUpload, { method: 'POST', body: formData });
      if (res.ok) {
        const data = await res.json();
        setUploadMsg({ type: 'success', text: `Successfully uploaded ${data.uploaded || files.length} student photo(s)` });
        fetchLogs();
      } else {
        setUploadMsg({ type: 'error', text: 'Upload failed. Check robot connection.' });
      }
    } catch {
      setUploadMsg({ type: 'error', text: 'Network error. Is Zoro online?' });
    }
    setUploading(false);
  };

  const handleDownload = (log: AttendanceLog) => {
    window.open(API.attendanceDownload(log.filename), '_blank');
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    handleUpload(e.dataTransfer.files);
  };

  // Mock data for demo when offline
  const displayLogs = logs.length > 0 ? logs : [
    { id: '1', filename: 'attendance_2026-04-27.csv', date: '2026-04-27', total_students: 32, present: 28, absent: 4 },
    { id: '2', filename: 'attendance_2026-04-26.csv', date: '2026-04-26', total_students: 32, present: 30, absent: 2 },
    { id: '3', filename: 'attendance_2026-04-25.csv', date: '2026-04-25', total_students: 32, present: 25, absent: 7 },
  ];

  return (
    <div>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 24 }}>
        <div>
          <h1 style={{ fontSize: 24, fontWeight: 800, color: '#fff', letterSpacing: '-0.02em' }}>
            Attendance
          </h1>
          <p style={{ fontSize: 13, color: '#666688', marginTop: 2 }}>
            Upload student photos for face recognition · Download attendance logs
          </p>
        </div>
        <button className="zoro-btn zoro-btn-secondary" onClick={fetchLogs}>
          <RefreshCw size={14} /> Refresh
        </button>
      </div>

      {/* Stats row */}
      {displayLogs.length > 0 && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 14, marginBottom: 24 }}>
          {[
            { label: 'Total Sessions', value: displayLogs.length, icon: FileText, color: 'var(--zoro-cyan)' },
            { label: 'Total Students', value: displayLogs[0]?.total_students || 0, icon: Users, color: '#a78bfa' },
            { label: 'Present Today', value: displayLogs[0]?.present || 0, icon: CheckCircle, color: 'var(--zoro-green)' },
            { label: 'Absent Today', value: displayLogs[0]?.absent || 0, icon: XCircle, color: 'var(--zoro-red)' },
          ].map(({ label, value, icon: Icon, color }) => (
            <div key={label} className="zoro-card" style={{ padding: '16px 18px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
                <Icon size={16} color={color} />
                <span style={{ fontSize: 11, color: '#666688', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', fontFamily: 'Space Mono, monospace' }}>
                  {label}
                </span>
              </div>
              <div style={{ fontSize: 28, fontWeight: 800, color: '#fff' }}>{value}</div>
            </div>
          ))}
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20 }}>
        {/* Upload section */}
        <div>
          <h2 style={{ fontSize: 15, fontWeight: 700, color: '#dde0f0', marginBottom: 12 }}>Upload Student Photos</h2>

          {/* Drop zone */}
          <div
            className={`drop-zone ${dragOver ? 'dragging' : ''}`}
            onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
            onDragLeave={() => setDragOver(false)}
            onDrop={handleDrop}
            onClick={() => fileInputRef.current?.click()}
            style={{ marginBottom: 12 }}
          >
            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept="image/*"
              style={{ display: 'none' }}
              onChange={e => handleUpload(e.target.files)}
            />
            <Upload size={28} color={dragOver ? 'var(--zoro-cyan)' : '#555577'} style={{ margin: '0 auto 10px' }} />
            <p style={{ fontSize: 14, color: dragOver ? 'var(--zoro-cyan)' : '#888899', fontWeight: 600 }}>
              Drop student photos here
            </p>
            <p style={{ fontSize: 12, color: '#555577', marginTop: 4 }}>
              JPG, PNG accepted · One photo per student · Name files as student name
            </p>
            {uploading && (
              <div style={{ marginTop: 12, fontSize: 12, color: 'var(--zoro-cyan)', fontFamily: 'Space Mono, monospace' }}>
                Uploading…
              </div>
            )}
          </div>

          {uploadMsg && (
            <div style={{
              padding: '10px 14px', borderRadius: 8, fontSize: 13,
              background: uploadMsg.type === 'success' ? 'rgba(74,222,128,0.1)' : 'rgba(248,113,113,0.1)',
              border: `1px solid ${uploadMsg.type === 'success' ? 'rgba(74,222,128,0.25)' : 'rgba(248,113,113,0.25)'}`,
              color: uploadMsg.type === 'success' ? 'var(--zoro-green)' : 'var(--zoro-red)',
              marginBottom: 12,
            }}>
              {uploadMsg.type === 'success' ? '✓' : '✕'} {uploadMsg.text}
            </div>
          )}

          <div className="zoro-card" style={{ padding: 14 }}>
            <p style={{ fontSize: 12, color: '#888899', lineHeight: 1.7 }}>
              <strong style={{ color: '#dde0f0' }}>How it works:</strong><br />
              1. Upload one clear face photo per student<br />
              2. Name each file as the student's name (e.g. <span style={{ color: 'var(--zoro-cyan)', fontFamily: 'Space Mono, monospace' }}>john_doe.jpg</span>)<br />
              3. Zoro uses face_recognition to match faces in class<br />
              4. Attendance is logged automatically each session
            </p>
          </div>
        </div>

        {/* Logs section */}
        <div>
          <h2 style={{ fontSize: 15, fontWeight: 700, color: '#dde0f0', marginBottom: 12 }}>Attendance Logs</h2>

          <div className="zoro-card" style={{ overflow: 'hidden' }}>
            {loading ? (
              <div style={{ padding: 30, textAlign: 'center', color: '#555577' }}>Loading logs…</div>
            ) : (
              <table className="zoro-table">
                <thead>
                  <tr>
                    <th>Date</th>
                    <th>Present</th>
                    <th>Absent</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {displayLogs.map(log => (
                    <tr key={log.id} style={{ cursor: 'pointer' }} onClick={() => fetchRecords(log)}>
                      <td>
                        <div style={{ fontWeight: 600, color: '#dde0f0' }}>{log.date}</div>
                        <div style={{ fontSize: 11, color: '#555577', fontFamily: 'Space Mono, monospace' }}>{log.filename}</div>
                      </td>
                      <td><span style={{ color: 'var(--zoro-green)', fontWeight: 600 }}>{log.present}</span></td>
                      <td><span style={{ color: log.absent > 0 ? 'var(--zoro-red)' : '#666688', fontWeight: 600 }}>{log.absent}</span></td>
                      <td>
                        <button
                          className="zoro-btn zoro-btn-secondary"
                          style={{ padding: '4px 10px', fontSize: 11 }}
                          onClick={(e) => { e.stopPropagation(); handleDownload(log); }}
                        >
                          <Download size={11} /> CSV
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      </div>

      {/* Selected log detail */}
      {selectedLog && (
        <div style={{ marginTop: 20 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
            <h2 style={{ fontSize: 15, fontWeight: 700, color: '#dde0f0' }}>
              Session: {selectedLog.date}
            </h2>
            <button className="zoro-btn zoro-btn-secondary" onClick={() => handleDownload(selectedLog)}>
              <Download size={14} /> Download CSV
            </button>
          </div>
          <div className="zoro-card" style={{ overflow: 'hidden' }}>
            {loadingRecords ? (
              <div style={{ padding: 30, textAlign: 'center', color: '#555577' }}>Loading records…</div>
            ) : records.length > 0 ? (
              <table className="zoro-table">
                <thead>
                  <tr>
                    <th>Student Name</th>
                    <th>Status</th>
                    <th>Time</th>
                    <th>Confidence</th>
                  </tr>
                </thead>
                <tbody>
                  {records.map((r, i) => (
                    <tr key={i}>
                      <td style={{ fontWeight: 600, color: '#dde0f0' }}>{r.name}</td>
                      <td>
                        <span style={{
                          display: 'inline-flex', alignItems: 'center', gap: 5,
                          color: r.status === 'present' ? 'var(--zoro-green)' : r.status === 'absent' ? 'var(--zoro-red)' : 'var(--zoro-amber)',
                          fontSize: 12, fontWeight: 600,
                        }}>
                          {r.status === 'present' ? <CheckCircle size={12} /> : r.status === 'absent' ? <XCircle size={12} /> : <Clock size={12} />}
                          {r.status.toUpperCase()}
                        </span>
                      </td>
                      <td style={{ fontFamily: 'Space Mono, monospace', fontSize: 12 }}>{r.time || '—'}</td>
                      <td style={{ fontFamily: 'Space Mono, monospace', fontSize: 12 }}>
                        {r.confidence !== undefined ? `${(r.confidence * 100).toFixed(0)}%` : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <div style={{ padding: 30, textAlign: 'center', color: '#555577' }}>
                No records for this session yet
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
