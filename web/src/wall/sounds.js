// Alert sounds for the wall tablet, synthesized with Web Audio (no audio files): a soft chime,
// a classic ding-dong, and urgent beeps that repeat until stopped (capped at about 2 minutes).
// Browsers keep audio locked until the page is touched once; the native wall shells allow autoplay.
//
// The audio output is only kept open while a sound plays: a running AudioContext counts as "media
// playing" to Android, and the tablet's own assistant ("Hey Google") stops listening while media
// plays (seen live on a Galaxy Tab). So it's suspended again a moment after each sound ends.

let ctx = null;
let busyUntil = 0;     // ctx.currentTime when the last scheduled sound ends
let alarms = 0;        // repeating alarms still going
let idleTimer = null;

function audio() {
  if (ctx) return ctx;
  const AC = window.AudioContext || window.webkitAudioContext;
  if (!AC) return null;
  try {
    ctx = new AC();
  } catch {
    ctx = null;
  }
  return ctx;
}

/** Suspend the audio output once nothing is playing (checked after each sound). */
function sleepWhenQuiet() {
  if (idleTimer) clearTimeout(idleTimer);
  const c = ctx;
  if (!c) return;
  const left = Math.max(0, busyUntil - c.currentTime);
  idleTimer = setTimeout(() => {
    idleTimer = null;
    if (ctx && !alarms && ctx.currentTime >= busyUntil && ctx.state === 'running') ctx.suspend().catch(() => {});
  }, (left + 0.5) * 1000);
}

/** Call once: the first touch anywhere unlocks audio for later alerts. */
export function unlockAudioOnFirstTouch() {
  const unlock = () => {
    const c = audio();
    if (c && c.state === 'suspended') c.resume().then(sleepWhenQuiet, () => {});
    window.removeEventListener('pointerdown', unlock, true);
    window.removeEventListener('keydown', unlock, true);
  };
  window.addEventListener('pointerdown', unlock, true);
  window.addEventListener('keydown', unlock, true);
}

/** One bell-like note: a sine with a soft attack and a long decay, plus a quiet overtone. */
function note(c, freq, at, dur, gain = 0.25, type = 'sine') {
  const g = c.createGain();
  g.gain.setValueAtTime(0.0001, at);
  g.gain.exponentialRampToValueAtTime(gain, at + 0.015);
  g.gain.exponentialRampToValueAtTime(0.0001, at + dur);
  g.connect(c.destination);
  [
    [freq, 1],
    [freq * 2.01, 0.25],
  ].forEach(([f, level]) => {
    const o = c.createOscillator();
    const og = c.createGain();
    o.type = type;
    o.frequency.setValueAtTime(f, at);
    og.gain.value = level;
    o.connect(og);
    og.connect(g);
    o.start(at);
    o.stop(at + dur + 0.05);
  });
  busyUntil = Math.max(busyUntil, at + dur + 0.05);
}

function ready() {
  const c = audio();
  if (!c) return null;
  if (idleTimer) {
    clearTimeout(idleTimer);
    idleTimer = null;
  }
  if (c.state === 'suspended') c.resume().catch(() => {});
  return c;
}

/** A soft two-note chime (a card). */
export function chime() {
  const c = ready();
  if (!c) return;
  const t = c.currentTime + 0.02;
  note(c, 880, t, 0.9, 0.16);
  note(c, 1318.5, t + 0.18, 1.2, 0.14);
  sleepWhenQuiet();
}

/** The classic ding-dong (a doorbell). */
export function doorbell() {
  const c = ready();
  if (!c) return;
  const t = c.currentTime + 0.02;
  note(c, 659.3, t, 1.4, 0.3);
  note(c, 523.3, t + 0.55, 1.8, 0.3);
  sleepWhenQuiet();
}

/** Urgent beeps every ~1.2 s until the returned stop() is called (or about 2 minutes pass). */
export function alarm() {
  const c = ready();
  if (!c) return () => {};
  let stopped = false;
  alarms += 1;
  const burst = () => {
    if (stopped) return;
    const t = c.currentTime + 0.02;
    for (let i = 0; i < 3; i += 1) note(c, 1046.5, t + i * 0.18, 0.14, 0.35, 'square');
  };
  burst();
  const timer = setInterval(burst, 1200);
  const cap = setTimeout(() => stop(), 120000);
  function stop() {
    if (stopped) return;
    stopped = true;
    alarms = Math.max(0, alarms - 1);
    clearInterval(timer);
    clearTimeout(cap);
    sleepWhenQuiet();
  }
  return stop;
}

/** Play an alert's sound by name; returns stop() (only the alarm keeps going). */
export function playSound(name) {
  if (name === 'alarm') return alarm();
  if (name === 'doorbell') doorbell();
  else if (name === 'chime') chime();
  return () => {};
}
