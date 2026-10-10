// Small pure helpers for the wall's voice timers (VoiceLayer.jsx).

/** A ringing timer stops making noise after this long (the card stays until it's tapped). */
export const RING_CAP_MS = 10 * 60 * 1000;

/** "7:42", "0:05", "1:02:05" for a number of seconds left (rounded up: 0:00 only at the end). */
export function countdown(seconds) {
  const s = Math.max(0, Math.ceil(seconds - 0.05));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = String(s % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${sec}` : `${m}:${sec}`;
}

/** The alarm's rhythm: beeps for 3 s, then the spoken "Your … timer is done", then 4 s of quiet. */
export function nextAlarmStep(step) {
  return step === 'beep' ? { ms: 3000, next: 'speak' } : { ms: 4000, next: 'beep' };
}
