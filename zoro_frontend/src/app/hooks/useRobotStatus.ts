import { useState, useEffect, useCallback } from 'react';
import { API, apiFetch } from '../lib/api';

export interface RobotStatus {
  online: boolean;
  voice_active: boolean;
  voice_enabled?: boolean;
  mode?: 'general' | 'teaching';
  teaching_active?: boolean;
  camera_active: boolean;
  ip: string;
  uptime?: string;
  students_present?: number;
  attendance_auto_scan?: {
    enabled: boolean;
    interval_minutes: number;
    last_run?: string | null;
    next_run?: string | null;
    last_result?: { marked?: string[] } | null;
    last_error?: string;
  };
}

export function useRobotStatus() {
  const [status, setStatus] = useState<RobotStatus>({
    online: false,
    voice_active: false,
    camera_active: false,
    ip: '—',
  });
  const [loading, setLoading] = useState(true);

  const fetchStatus = useCallback(async () => {
    const { data } = await apiFetch<RobotStatus>(API.status);
    if (data) {
      setStatus(data);
    } else {
      setStatus(s => ({ ...s, online: false }));
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    fetchStatus();
    const interval = setInterval(fetchStatus, 5000);
    return () => clearInterval(interval);
  }, [fetchStatus]);

  return { status, loading, refresh: fetchStatus };
}
