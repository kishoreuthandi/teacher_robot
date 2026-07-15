import { NavLink } from 'react-router';
import {
  Video, Users, BookOpen, Mic2, MessageSquare, Settings, Activity, Zap, Brain, ClipboardList, FileCheck
} from 'lucide-react';
import { useRobotStatus } from '../hooks/useRobotStatus';

const navItems = [
  { to: '/', icon: Video, label: 'Live Camera' },
  { to: '/attendance', icon: Users, label: 'Attendance' },
  { to: '/syllabus', icon: BookOpen, label: 'Syllabus & AI' },
  { to: '/classroom', icon: Brain, label: 'Classroom Brain' },
  { to: '/behavior', icon: ClipboardList, label: 'Behavior Report' },
  { to: '/assessments', icon: FileCheck, label: 'Assessments' },
  { to: '/speech', icon: Mic2, label: 'Special Speech' },
  { to: '/transcripts', icon: MessageSquare, label: 'Transcripts' },
];

export default function Sidebar() {
  const { status } = useRobotStatus();

  return (
    <aside className="sidebar">
      {/* Logo */}
      <div style={{ padding: '4px 14px 20px', borderBottom: '1px solid var(--zoro-border)', marginBottom: 8 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{
            width: 36, height: 36, borderRadius: 8,
            background: 'var(--zoro-cyan-dim)',
            border: '1px solid var(--zoro-border-hover)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}>
            <Zap size={18} color="var(--zoro-cyan)" />
          </div>
          <div>
            <div style={{ fontSize: 16, fontWeight: 800, color: '#fff', letterSpacing: '-0.02em' }}>
              ZORO
            </div>
            <div style={{ fontSize: 10, color: '#666688', fontFamily: 'Space Mono, monospace', marginTop: -2 }}>
              2026 · Classroom AI
            </div>
          </div>
        </div>

        {/* Robot status */}
        <div style={{
          marginTop: 14, padding: '8px 12px',
          background: 'var(--zoro-surface-3)', borderRadius: 8,
          border: '1px solid var(--zoro-border)',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
              <span style={{
                width: 8, height: 8, borderRadius: '50%',
                background: status.online ? 'var(--zoro-green)' : 'var(--zoro-red)',
                boxShadow: status.online ? '0 0 6px var(--zoro-green)' : 'none',
                display: 'block',
                animation: status.online ? 'pulse-dot 2s infinite' : 'none',
              }} />
              <span style={{ fontSize: 12, fontWeight: 600, color: status.online ? 'var(--zoro-green)' : 'var(--zoro-red)' }}>
                {status.online ? 'ONLINE' : 'OFFLINE'}
              </span>
            </div>
            <Activity size={12} color="#555577" />
          </div>
          <div style={{ fontSize: 11, color: '#555577', fontFamily: 'Space Mono, monospace', marginTop: 4 }}>
            {status.ip}
          </div>
        </div>
      </div>

      {/* Nav */}
      <div className="section-label">Navigation</div>
      {navItems.map(({ to, icon: Icon, label }) => (
        <NavLink
          key={to}
          to={to}
          end={to === '/'}
          className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
        >
          <Icon size={16} />
          {label}
        </NavLink>
      ))}

      {/* Voice status */}
      <div style={{ marginTop: 'auto' }}>
        <div className="section-label">Voice Agent</div>
        <div style={{
          padding: '10px 14px', borderRadius: 8,
          background: status.voice_active ? 'rgba(74,222,128,0.08)' : 'var(--zoro-surface-3)',
          border: `1px solid ${status.voice_active ? 'rgba(74,222,128,0.2)' : 'var(--zoro-border)'}`,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Mic2 size={14} color={status.voice_enabled === false ? 'var(--zoro-red)' : status.voice_active ? 'var(--zoro-green)' : '#555577'} />
            <span style={{ fontSize: 12, color: status.voice_enabled === false ? 'var(--zoro-red)' : status.voice_active ? 'var(--zoro-green)' : '#555577', fontWeight: 600 }}>
              {status.voice_enabled === false ? 'Voice Muted' : status.voice_active ? 'Listening…' : 'Voice Ready'}
            </span>
          </div>
        </div>

        <div className="section-label" style={{ marginTop: 12 }}>Settings</div>
        <NavLink to="/settings" className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>
          <Settings size={16} />
          Configuration
        </NavLink>
      </div>
    </aside>
  );
}
