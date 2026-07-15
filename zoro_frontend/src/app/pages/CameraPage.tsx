import { useState, useRef, useEffect, useCallback } from 'react';
import { API, apiFetch } from '../lib/api';
import { useRobotStatus } from '../hooks/useRobotStatus';
import {
  Camera, Download, Square, ArrowUp, ArrowDown,
  ArrowLeft, ArrowRight, RotateCcw, Wifi, WifiOff, Eye, EyeOff, ScanFace, Gauge,
  UserCheck, Users, TrendingUp, Clock
} from 'lucide-react';

export default function CameraPage() {
  const { status } = useRobotStatus();
  const [streamError, setStreamError] = useState(false);
  const [streamRetry, setStreamRetry] = useState(0);
  const [showOverlay, setShowOverlay] = useState(true);
  const [movingDir, setMovingDir] = useState<string | null>(null);
  const [scanMsg, setScanMsg] = useState<{ text: string; ok: boolean } | null>(null);
  const [voiceBusy, setVoiceBusy] = useState(false);
  const [motorSpeed, setMotorSpeed] = useState(0.55);
  const imgRef = useRef<HTMLImageElement>(null);
  const movingDirRef = useRef<string | null>(null);
  const motorSpeedRef = useRef(motorSpeed);
  const motorWsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    motorSpeedRef.current = motorSpeed;
  }, [motorSpeed]);

  const handleSnapshot = async () => {
    const a = document.createElement('a');
    a.href = API.snapshot + '?t=' + Date.now();
    a.download = `zoro_snapshot_${new Date().toISOString().slice(0,19).replace(/:/g,'-')}.jpg`;
    a.click();
  };

  useEffect(() => {
    if (!status.online || !status.camera_active || !streamError) return;
    const retry = window.setTimeout(() => {
      setStreamError(false);
      setStreamRetry((value) => value + 1);
    }, 1500);
    return () => window.clearTimeout(retry);
  }, [status.online, status.camera_active, streamError]);

  const markAttendance = async () => {
    try {
      const res = await fetch(`${API.base}/attendance/mark-from-robot`, { method: 'POST' });
      const data = await res.json();
      if (data.marked && data.marked.length > 0) {
        setScanMsg({ text: `✓ Marked: ${data.marked.join(', ')}`, ok: true });
      } else {
        setScanMsg({ text: '⚠ No known face detected', ok: false });
      }
      setTimeout(() => setScanMsg(null), 3000);
    } catch {
      setScanMsg({ text: '✕ Scan failed', ok: false });
      setTimeout(() => setScanMsg(null), 3000);
    }
  };

  const autoScan = status.attendance_auto_scan?.enabled ?? false;

  const sendMotorSocket = useCallback((direction: string) => {
    const ws = motorWsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return false;
    ws.send(JSON.stringify({ direction, speed: motorSpeedRef.current }));
    return true;
  }, []);

  const move = useCallback(async (direction: string) => {
    movingDirRef.current = direction;
    setMovingDir(direction);
    if (sendMotorSocket(direction)) return;
    const { data } = await apiFetch<{ ok?: boolean; blocked?: boolean; message?: string }>(API.motorMove, {
      method: 'POST',
      body: JSON.stringify({ direction, speed: motorSpeed }),
    });
    if (data?.blocked) {
      movingDirRef.current = null;
      setMovingDir(null);
      setScanMsg({ text: data.message || 'Movement blocked by camera safety', ok: false });
      setTimeout(() => setScanMsg(null), 3000);
    }
  }, [motorSpeed, sendMotorSocket]);

  const stopMotors = useCallback(async () => {
    movingDirRef.current = null;
    setMovingDir(null);
    if (sendMotorSocket('stop')) return;
    await apiFetch(API.motorStop, { method: 'POST' });
  }, [sendMotorSocket]);

  useEffect(() => {
    let reconnectTimer: number | undefined;
    let closed = false;

    const connect = () => {
      if (closed) return;
      const ws = new WebSocket(API.motorSocket);
      motorWsRef.current = ws;
      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data?.blocked) {
            movingDirRef.current = null;
            setMovingDir(null);
            setScanMsg({ text: data.message || 'Movement blocked by camera safety', ok: false });
            setTimeout(() => setScanMsg(null), 3000);
          }
        } catch {
          // Ignore non-JSON motor acknowledgements.
        }
      };
      ws.onclose = () => {
        if (motorWsRef.current === ws) motorWsRef.current = null;
        if (!closed) reconnectTimer = window.setTimeout(connect, 800);
      };
      ws.onerror = () => ws.close();
    };

    connect();
    return () => {
      closed = true;
      if (reconnectTimer) window.clearTimeout(reconnectTimer);
      motorWsRef.current?.close();
      motorWsRef.current = null;
    };
  }, []);

  useEffect(() => {
    const keyMap: Record<string, string> = {
      w: 'forward',
      a: 'left',
      s: 'backward',
      d: 'right',
      r: 'rotate',
    };
    const isTyping = (target: EventTarget | null) => {
      const tag = target instanceof HTMLElement ? target.tagName.toLowerCase() : '';
      return tag === 'input' || tag === 'textarea' || tag === 'select';
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.repeat || isTyping(event.target)) return;
      if (event.code === 'Space') {
        event.preventDefault();
        void stopMotors();
        return;
      }
      const direction = keyMap[event.key.toLowerCase()];
      if (!direction) return;
      event.preventDefault();
      if (movingDirRef.current !== direction) void move(direction);
    };
    const onKeyUp = (event: KeyboardEvent) => {
      const direction = keyMap[event.key.toLowerCase()];
      if (!direction || movingDirRef.current !== direction) return;
      event.preventDefault();
      void stopMotors();
    };
    window.addEventListener('keydown', onKeyDown);
    window.addEventListener('keyup', onKeyUp);
    return () => {
      window.removeEventListener('keydown', onKeyDown);
      window.removeEventListener('keyup', onKeyUp);
    };
  }, [move, stopMotors]);

  const toggleVoice = async () => {
    setVoiceBusy(true);
    await apiFetch(status.voice_enabled === false ? API.voiceStart : API.voiceStop, { method: 'POST' });
    setTimeout(() => {
      setVoiceBusy(false);
      window.location.reload();
    }, 500);
  };

  const toggleAutoScan = async () => {
    const enabled = !(status.attendance_auto_scan?.enabled ?? false);
    const { data, error } = await apiFetch<{ enabled: boolean }>(API.attendanceAutoScan, {
      method: 'POST',
      body: JSON.stringify({ enabled, interval_minutes: 45 }),
    });
    if (error || !data) {
      setScanMsg({ text: 'Auto scan update failed', ok: false });
    } else {
      setScanMsg({ text: enabled ? '45-minute period attendance enabled' : 'Period attendance paused', ok: true });
    }
    setTimeout(() => setScanMsg(null), 3000);
  };

  const runPeriodScanNow = async () => {
    const { data, error } = await apiFetch<{ marked?: string[] }>(`${API.attendanceAutoScanRunNow}?duration_seconds=3`, { method: 'POST' });
    if (error || !data) {
      setScanMsg({ text: 'Period scan failed', ok: false });
    } else {
      setScanMsg({ text: `3-second scan marked: ${data.marked?.join(', ') || 'no known face'}`, ok: true });
    }
    setTimeout(() => setScanMsg(null), 3000);
  };

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 24 }}>
        <div>
          <h1 style={{ fontSize: 24, fontWeight: 800, color: '#fff', letterSpacing: '-0.02em' }}>
            Live Camera
          </h1>
          <p style={{ fontSize: 13, color: '#666688', marginTop: 2 }}>
            Real-time MJPEG stream from Zoro's camera
          </p>
        </div>
        <div style={{ display: 'flex', gap: 10 }}>
          <button className="zoro-btn zoro-btn-secondary" onClick={() => setShowOverlay(v => !v)}>
            {showOverlay ? <EyeOff size={14} /> : <Eye size={14} />}
            {showOverlay ? 'Hide HUD' : 'Show HUD'}
          </button>
          <button className="zoro-btn zoro-btn-secondary" onClick={handleSnapshot}>
            <Download size={14} /> Snapshot
          </button>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 280px', gap: 20 }}>
        {/* Video feed */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div className="zoro-card" style={{ overflow: 'hidden', position: 'relative' }}>
            <div className="scanline" style={{ position: 'relative', background: '#000', minHeight: 420, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            {status.online && status.camera_active && !streamError ? (
              <img
                ref={imgRef}
                src={`${API.videoStream}?retry=${streamRetry}`}
                alt="Live camera feed"
                onError={() => setStreamError(true)}
                onLoad={() => setStreamError(false)}
                style={{ width: '100%', height: 'auto', display: 'block', maxHeight: 520, objectFit: 'contain' }}
              />
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12, color: '#555577', padding: 60 }}>
                <WifiOff size={40} />
                <span style={{ fontSize: 14 }}>
                  {status.online ? 'Camera stream unavailable' : 'Robot offline'}
                </span>
                <span style={{ fontSize: 11, fontFamily: 'Space Mono, monospace' }}>
                  {API.videoStream}
                </span>
              </div>
            )}

            {showOverlay && (
              <>
                <div style={{ position: 'absolute', top: 14, left: 14, display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{
                    background: status.online ? 'rgba(74,222,128,0.9)' : 'rgba(248,113,113,0.9)',
                    color: '#000', fontSize: 10, fontWeight: 700,
                    fontFamily: 'Space Mono, monospace',
                    padding: '3px 8px', borderRadius: 4,
                  }}>
                    {status.online ? '● LIVE' : '✕ OFFLINE'}
                  </span>
                  {autoScan && (
                    <span style={{
                      background: 'rgba(0,229,255,0.9)', color: '#000',
                      fontSize: 10, fontWeight: 700, fontFamily: 'Space Mono, monospace',
                      padding: '3px 8px', borderRadius: 4,
                    }}>
                      AUTO SCAN ON
                    </span>
                  )}
                </div>
                <div style={{
                  position: 'absolute', bottom: 14, left: 14,
                  fontFamily: 'Space Mono, monospace', fontSize: 10, color: 'rgba(255,255,255,0.5)',
                }}>
                  ZORO CAM · {new Date().toLocaleTimeString()}
                </div>
                <div style={{
                  position: 'absolute', bottom: 14, right: 14,
                  fontFamily: 'Space Mono, monospace', fontSize: 10, color: 'rgba(255,255,255,0.5)',
                }}>
                  FINGERS 720 · /dev/video0
                </div>
              </>
            )}
          </div>

          {/* Scan message */}
          {scanMsg && (
            <div style={{
              padding: '8px 14px',
              background: scanMsg.ok ? 'rgba(74,222,128,0.15)' : 'rgba(255,171,0,0.15)',
              borderTop: `1px solid ${scanMsg.ok ? 'rgba(74,222,128,0.3)' : 'rgba(255,171,0,0.3)'}`,
              color: scanMsg.ok ? 'var(--zoro-green)' : 'var(--zoro-amber)',
              fontSize: 13, fontWeight: 600,
            }}>
              {scanMsg.text}
            </div>
          )}

          {/* Stream URL bar */}
            <div style={{
              padding: '10px 14px', background: 'var(--zoro-surface-3)',
              borderTop: '1px solid var(--zoro-border)',
              display: 'flex', alignItems: 'center', gap: 8,
            }}>
              {status.online ? <Wifi size={12} color="var(--zoro-green)" /> : <WifiOff size={12} color="var(--zoro-red)" />}
              <span style={{ fontSize: 11, fontFamily: 'Space Mono, monospace', color: '#666688' }}>
                {API.videoStream}
              </span>
              <button
                className="zoro-btn zoro-btn-secondary"
                style={{ marginLeft: 'auto', padding: '4px 12px', fontSize: 11 }}
                onClick={() => { setStreamError(false); if (imgRef.current) { const s = imgRef.current.src; imgRef.current.src = ''; imgRef.current.src = s; }}}
              >
                <RotateCcw size={11} /> Reload
              </button>
            </div>
          </div>

          <DashboardCharts
            present={status.students_present ?? 0}
            cameraActive={Boolean(status.camera_active)}
            voiceReady={status.voice_enabled !== false}
            autoScan={autoScan}
          />
        </div>

        {/* Right panel */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {/* Status card */}
          <div className="zoro-card" style={{ padding: 16 }}>
            <h3 style={{ fontSize: 12, fontWeight: 700, color: '#666688', textTransform: 'uppercase', letterSpacing: '0.08em', fontFamily: 'Space Mono, monospace', marginBottom: 12 }}>
              Robot Status
            </h3>
            {[
              { label: 'Camera', value: status.camera_active ? 'Active' : 'Inactive', ok: status.camera_active },
              { label: 'Voice Agent', value: status.voice_enabled === false ? 'Muted' : status.voice_active ? 'Listening' : 'Ready', ok: status.voice_enabled !== false },
              { label: 'Connection', value: status.online ? 'Online' : 'Offline', ok: status.online },
              { label: 'Students', value: status.students_present !== undefined ? String(status.students_present) : '—', ok: true },
            ].map(({ label, value, ok }) => (
              <div key={label} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                <span style={{ fontSize: 12, color: '#888899' }}>{label}</span>
                <span style={{ fontSize: 12, fontWeight: 600, color: ok ? '#dde0f0' : 'var(--zoro-red)' }}>{value}</span>
              </div>
            ))}
            <button
              className={`zoro-btn ${status.voice_enabled === false ? 'zoro-btn-primary' : 'zoro-btn-danger'}`}
              style={{ width: '100%', marginTop: 10 }}
              onClick={toggleVoice}
              disabled={voiceBusy}
            >
              {status.voice_enabled === false ? 'Turn Voice On' : 'Exam Mode: Mute Voice'}
            </button>
          </div>

          {/* Attendance */}
          <div className="zoro-card" style={{ padding: 16 }}>
            <h3 style={{ fontSize: 12, fontWeight: 700, color: '#666688', textTransform: 'uppercase', letterSpacing: '0.08em', fontFamily: 'Space Mono, monospace', marginBottom: 12 }}>
              Attendance
            </h3>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              <button
                className="zoro-btn zoro-btn-secondary"
                style={{ width: '100%', justifyContent: 'flex-start' }}
                onClick={markAttendance}
              >
                <ScanFace size={14} /> Mark Attendance Now
              </button>
              <button
                className={`zoro-btn ${autoScan ? 'zoro-btn-primary' : 'zoro-btn-secondary'}`}
                style={{ width: '100%', justifyContent: 'flex-start' }}
                onClick={toggleAutoScan}
              >
                <Camera size={14} /> {autoScan ? '⏹ Pause 45-min Scan' : '▶ Enable 45-min Scan'}
              </button>
              <button
                className="zoro-btn zoro-btn-secondary"
                style={{ width: '100%', justifyContent: 'flex-start' }}
                onClick={runPeriodScanNow}
              >
                <ScanFace size={14} /> Run 3-second Scan
              </button>
              {autoScan && (
                <p style={{ fontSize: 11, color: 'var(--zoro-cyan)', fontFamily: 'Space Mono, monospace', textAlign: 'center' }}>
                  Every 45 minutes · Next: {status.attendance_auto_scan?.next_run ? new Date(status.attendance_auto_scan.next_run).toLocaleTimeString() : 'scheduled'}
                </p>
              )}
            </div>
          </div>

          {/* Motor control */}
          <div className="zoro-card" style={{ padding: 16 }}>
            <h3 style={{ fontSize: 12, fontWeight: 700, color: '#666688', textTransform: 'uppercase', letterSpacing: '0.08em', fontFamily: 'Space Mono, monospace', marginBottom: 14 }}>
              Motor Control
            </h3>
            <div style={{ marginBottom: 14 }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
                <span style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: '#888899', fontWeight: 600 }}>
                  <Gauge size={13} /> Speed
                </span>
                <span style={{ fontSize: 12, color: 'var(--zoro-cyan)', fontFamily: 'Space Mono, monospace', fontWeight: 700 }}>
                  {Math.round(motorSpeed * 100)}%
                </span>
              </div>
              <input
                type="range"
                min="0.25"
                max="1"
                step="0.05"
                value={motorSpeed}
                onChange={(event) => setMotorSpeed(Number(event.target.value))}
                style={{ width: '100%', accentColor: 'var(--zoro-cyan)' }}
                aria-label="Motor speed"
              />
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 6, marginTop: 8 }}>
                {[
                  { label: 'Slow', value: 0.35 },
                  { label: 'Normal', value: 0.55 },
                  { label: 'Fast', value: 0.8 },
                ].map((preset) => (
                  <button
                    key={preset.label}
                    className={`zoro-btn ${Math.abs(motorSpeed - preset.value) < 0.01 ? 'zoro-btn-primary' : 'zoro-btn-secondary'}`}
                    style={{ padding: '6px 4px', fontSize: 10 }}
                    onClick={() => setMotorSpeed(preset.value)}
                  >
                    {preset.label}
                  </button>
                ))}
              </div>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gridTemplateRows: '1fr 1fr 1fr', gap: 6, maxWidth: 180, margin: '0 auto' }}>
              <div />
              <MotorBtn dir="forward" active={movingDir === 'forward'} onPress={move} onRelease={stopMotors}>
                <ArrowUp size={16} />
              </MotorBtn>
              <div />
              <MotorBtn dir="left" active={movingDir === 'left'} onPress={move} onRelease={stopMotors}>
                <ArrowLeft size={16} />
              </MotorBtn>
              <button
                className="zoro-btn zoro-btn-danger"
                style={{ aspectRatio: '1', padding: 0, fontSize: 10 }}
                onClick={stopMotors}
              >
                <Square size={14} />
              </button>
              <MotorBtn dir="right" active={movingDir === 'right'} onPress={move} onRelease={stopMotors}>
                <ArrowRight size={16} />
              </MotorBtn>
              <div />
              <MotorBtn dir="backward" active={movingDir === 'backward'} onPress={move} onRelease={stopMotors}>
                <ArrowDown size={16} />
              </MotorBtn>
              <div />
            </div>
            <button
              className={`zoro-btn ${movingDir === 'rotate' ? 'zoro-btn-primary' : 'zoro-btn-secondary'}`}
              style={{ width: '100%', marginTop: 10 }}
              onMouseDown={() => move('rotate')}
              onMouseUp={stopMotors}
              onMouseLeave={stopMotors}
              onTouchStart={() => move('rotate')}
              onTouchEnd={stopMotors}
            >
              <RotateCcw size={14} /> Rotate Scan
            </button>
            <p style={{ fontSize: 10, color: '#555577', textAlign: 'center', marginTop: 10, fontFamily: 'Space Mono, monospace' }}>
              HOLD to move · RELEASE to stop
            </p>
            <div style={{ marginTop: 10, padding: '8px', background: 'var(--zoro-surface-3)', borderRadius: 6, fontSize: 11, color: '#555577', textAlign: 'center' }}>
              Keyboard: <span style={{ color: 'var(--zoro-cyan)', fontFamily: 'Space Mono, monospace' }}>W A S D</span> - Space stops
            </div>
          </div>

          {/* Quick actions */}
          <div className="zoro-card" style={{ padding: 16 }}>
            <h3 style={{ fontSize: 12, fontWeight: 700, color: '#666688', textTransform: 'uppercase', letterSpacing: '0.08em', fontFamily: 'Space Mono, monospace', marginBottom: 12 }}>
              Quick Actions
            </h3>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              <button className="zoro-btn zoro-btn-secondary" style={{ width: '100%', justifyContent: 'flex-start' }} onClick={handleSnapshot}>
                <Download size={14} /> Save Snapshot
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function DashboardCharts({ present, cameraActive, voiceReady, autoScan }: {
  present: number; cameraActive: boolean; voiceReady: boolean; autoScan: boolean;
}) {
  const total = Math.max(32, present + 28);
  const absent = Math.max(0, total - present);
  const attendancePercent = Math.round((present / total) * 100);
  const trend = [62, 68, 74, 71, 80, attendancePercent];
  const points = trend.map((value, index) => `${index * 48},${96 - value}`).join(' ');
  const activities = [
    cameraActive ? 'Camera perception active' : 'Camera waiting for stream',
    voiceReady ? 'Voice agent ready' : 'Voice muted for exam mode',
    autoScan ? 'Auto attendance scan running' : 'Manual attendance mode',
  ];

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: 14 }}>
      <div className="zoro-card" style={{ padding: 16, minHeight: 150 }}>
        <h3 style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, fontWeight: 700, color: '#8888aa', textTransform: 'uppercase', letterSpacing: '0.08em', fontFamily: 'Space Mono, monospace', marginBottom: 14 }}>
          <Users size={14} /> Attendance Overview
        </h3>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <div style={{
            width: 78, height: 78, borderRadius: '50%',
            background: `conic-gradient(var(--zoro-green) 0 ${attendancePercent}%, var(--zoro-red) ${attendancePercent}% 100%)`,
            display: 'grid', placeItems: 'center',
          }}>
            <div style={{ width: 48, height: 48, borderRadius: '50%', background: 'var(--zoro-surface-1)', display: 'grid', placeItems: 'center', color: '#fff', fontWeight: 800 }}>
              {attendancePercent}%
            </div>
          </div>
          <div style={{ flex: 1 }}>
            <MetricRow label="Present" value={present} color="var(--zoro-green)" />
            <MetricRow label="Absent" value={absent} color="var(--zoro-red)" />
            <MetricRow label="Total" value={total} color="var(--zoro-cyan)" />
          </div>
        </div>
      </div>

      <div className="zoro-card" style={{ padding: 16, minHeight: 150 }}>
        <h3 style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, fontWeight: 700, color: '#8888aa', textTransform: 'uppercase', letterSpacing: '0.08em', fontFamily: 'Space Mono, monospace', marginBottom: 12 }}>
          <TrendingUp size={14} /> Weekly Trend
        </h3>
        <svg viewBox="0 0 240 110" role="img" aria-label="Weekly attendance trend" style={{ width: '100%', height: 92, display: 'block' }}>
          {[0, 1, 2].map((line) => (
            <line key={line} x1="0" x2="240" y1={24 + line * 30} y2={24 + line * 30} stroke="rgba(255,255,255,0.08)" />
          ))}
          <polyline points={points} fill="none" stroke="var(--zoro-cyan)" strokeWidth="4" strokeLinecap="round" strokeLinejoin="round" />
          {trend.map((value, index) => (
            <circle key={`${value}-${index}`} cx={index * 48} cy={96 - value} r="4" fill="var(--zoro-green)" />
          ))}
        </svg>
      </div>

      <div className="zoro-card" style={{ padding: 16, minHeight: 150 }}>
        <h3 style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, fontWeight: 700, color: '#8888aa', textTransform: 'uppercase', letterSpacing: '0.08em', fontFamily: 'Space Mono, monospace', marginBottom: 12 }}>
          <Clock size={14} /> Today Activity
        </h3>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {activities.map((item) => (
            <div key={item} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, color: '#dde0f0' }}>
              <UserCheck size={14} color="var(--zoro-cyan)" />
              <span>{item}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function MetricRow({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
      <span style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: '#888899' }}>
        <span style={{ width: 7, height: 7, borderRadius: '50%', background: color }} />
        {label}
      </span>
      <span style={{ color: '#fff', fontWeight: 800 }}>{value}</span>
    </div>
  );
}

function MotorBtn({ dir, active, onPress, onRelease, children }: {
  dir: string; active: boolean;
  onPress: (d: string) => void; onRelease: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      className={`zoro-btn ${active ? 'zoro-btn-primary' : 'zoro-btn-secondary'}`}
      style={{ aspectRatio: '1', padding: 0 }}
      onMouseDown={() => onPress(dir)}
      onMouseUp={onRelease}
      onMouseLeave={onRelease}
      onTouchStart={() => onPress(dir)}
      onTouchEnd={onRelease}
    >
      {children}
    </button>
  );
}
