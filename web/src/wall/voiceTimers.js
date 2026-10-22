// Small pure helpers for the wall's voice timers (VoiceLayer.jsx, AlarmsTimers.jsx).

/** "7:42", "0:05", "1:02:05" for a number of seconds left (rounded up: 0:00 only at the end). */
export function countdown(seconds) {
  const s = Math.max(0, Math.ceil(seconds - 0.05));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = String(s % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${sec}` : `${m}:${sec}`;
}
