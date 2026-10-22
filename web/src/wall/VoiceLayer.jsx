// Voice on the wall tablet: the listening card at the bottom, the timer and alarm cards, the ringing
// screen, the Alarms & timers sheets (AlarmsTimers.jsx), and the line that says when it can't listen.
//
// The wall app (Android or iPad) listens for its wake word ("Hey Newman" unless the tablet's Voice
// settings pick another; api/voice -> wakeword.phrase) on the tablet itself and talks to this page:
//   window.NewmanVoice.event({type: 'wake', followUp?})          the wake word was heard (it chimed), or it
//                                                                listens for the answer to a question (followUp)
//   window.NewmanVoice.event({type: 'level', value: 0..1})       how loud it is (the animation)
//   window.NewmanVoice.event({type: 'audio', pcm: base64, rate}) what was said (16-bit mono PCM)
//   window.NewmanVoice.event({type: 'cancel', quiet?})           nothing was said (quiet: after a question)
// and this page answers through window.NewmanWall: voiceConfig(json) (on/off, threshold, version - a
// new one, e.g. another wake word chosen, makes the app fetch api/voice/wakeword again), voiceDone(listen)
// (the reply finished - listen for the wake word again, or with `listen` for the answer to the question
// it asked, no wake word needed). The page sends the audio to the hub (POST api/voice/utterance), shows
// what was heard and the reply, speaks it and does the action (a camera full screen, a page, Alarms…).
//
// Timers and alarms live on the hub per tablet (api/voice/timers, api/voice/alarms, {type: "timers"} and
// {type: "alarms"} pushes). One that rings shows the ringing screen and rings through the app
// (NewmanWall.ring({kind, id, title, ringMs}) / stopRing(): the tablet's alarm sound on its alarm volume,
// the screen fully bright) - or with beeps here in a plain browser. A timer also says "Your pasta timer is
// done" once. It rings until Stop (a tap or "Hey Newman, stop"), up to the app's "Stop ringing after"; the
// app's settings (NewmanWall.ringSettings(): snooze and ring minutes) go to the hub (PUT api/voice/ringing).
// The coming alarms go to the app too (NewmanWall.alarmSchedule) so it can ring one itself if the page
// isn't there; and this page rings an alarm itself LOCAL_AFTER_S after its time when the hub hasn't said
// (the hub unreachable). While the card, the ringing screen or a sheet shows, the wall counts as in use.
// The app tells the page when it comes back to the front (a 'newmanwall-resume' window event):
// the microphone may have been allowed meanwhile (NewmanWall.micAllowed(), openMicSettings()).
//
// The timers running on the household's Echos (an Amazon Alexa connection: GET api/alexa/timers and
// {type: "alexaTimers"} pushes) show with them: which Echo, no ✕ (they're Alexa's), and at zero "Done ·
// Ringing on <Echo>" until Amazon drops it - silent here, the Echo rings.
// {type: "voice-teach"} pushes (from the dashboard) open "Teach Hey Newman" (TeachVoice.jsx); the
// app's clip events ({type: "clip" | "clip-level" | "clip-none" | "clip-error"}) go to it, and the
// wake word isn't listened for until it closes.

import { useCallback, useEffect, useRef, useState } from 'react';
import { api, enc, tabletToken } from '../api.js';
import { navigate, useApi } from '../hooks.js';
import { useLive } from '../live.js';
import { Icon } from '../icons.jsx';
import { alarm as beeps } from './sounds.js';
import { PersonMap } from './PeopleTile.jsx';
import { countdown } from './voiceTimers.js';
import TeachVoice from './TeachVoice.jsx';
import { duck, setVoiceCard, openSheet } from './music.js';
import { alarmCards, alarmSchedule, localRings, ringingNow, timerLeft } from './alarmTimes.js';
import { AlarmCard, AlarmEditSheet, AlarmsSheet, alarmApi, NewTimerSheet, RingScreen, TimerSheet, timerApi, VoiceProblem } from './AlarmsTimers.jsx';
import './voice.css';

const HIDE_AFTER_MS = 4500;
const RECHECK_MS = 30000; // while it can't listen: ask the hub (and the microphone) again this often
const DEFAULT_RINGING = { snoozeMinutes: 9, ringMinutes: 10 };

function native() {
  const n = window.NewmanWall;
  return n && n.native ? n : null;
}

function b64ToBytes(b64) {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i += 1) out[i] = bin.charCodeAt(i);
  return out;
}

async function sendUtterance(body, contentType) {
  const tok = tabletToken();
  const res = await fetch('api/voice/utterance', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': contentType, Accept: 'application/json', ...(tok ? { Authorization: 'Bearer ' + tok } : {}) },
    body,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `The hub couldn't answer (${res.status}).`);
  return data;
}

/** Plays a {type, data} audio answer; resolves when it ends (or fails). Returns {done, stop}. */
function play(audio, volume) {
  if (!audio || !audio.data) return { done: Promise.resolve(), stop: () => {} };
  const el = new Audio(`data:${audio.type || 'audio/wav'};base64,${audio.data}`);
  el.volume = Math.max(0, Math.min(1, (volume ?? 80) / 100));
  let finish;
  const done = new Promise((resolve) => {
    finish = resolve;
  });
  el.onended = () => finish();
  el.onerror = () => finish();
  el.play().catch(() => finish());
  return {
    done,
    stop: () => {
      el.pause();
      finish();
    },
  };
}

/** The wall app's own "Snooze for" / "Stop ringing after", or null outside it (or an older app). */
function nativeRinging() {
  const n = native();
  if (!n || typeof n.ringSettings !== 'function') return null;
  try {
    const s = JSON.parse(n.ringSettings() || '{}');
    return s && s.snoozeMinutes && s.ringMinutes ? { snoozeMinutes: Number(s.snoozeMinutes), ringMinutes: Number(s.ringMinutes) } : null;
  } catch {
    return null;
  }
}

/** Whether the app may use the microphone: true / false, or null when it can't say (a browser, an older app). */
function micAllowed() {
  const n = native();
  if (!n || typeof n.micAllowed !== 'function') return null;
  try {
    return n.micAllowed() === true;
  } catch {
    return null;
  }
}

/* ---------------- the listening card ---------------- */

function Orb({ phase, level }) {
  const scale = phase === 'listening' ? 1 + Math.min(1, level) * 0.45 : 1;
  return (
    <span className={'vo-orb is-' + phase} aria-hidden="true">
      <span className="vo-orb-ring" />
      <span className="vo-orb-core" style={{ transform: `scale(${scale.toFixed(3)})` }} />
      <svg className="vo-orb-mic" viewBox="0 0 24 24" width="26" height="26">
        <rect x="9" y="3" width="6" height="11" rx="3" fill="currentColor" />
        <path d="M6 11a6 6 0 0 0 12 0M12 17v4M9 21h6" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round" />
      </svg>
    </span>
  );
}

const PHASE_TEXT = { listening: 'Listening…', thinking: 'One moment…', speaking: '', done: '', error: '' };

function VoiceCard({ s, over, onClose }) {
  if (s.phase === 'idle') return null;
  return (
    <div className={'vo-card is-' + s.phase + (over ? ' is-over' : '')} role="dialog" aria-label="Newman is listening" onPointerDown={onClose}>
      <Orb phase={s.phase} level={s.level} />
      <div className="vo-text">
        {s.heard ? <div className="vo-heard">“{s.heard}”</div> : <div className="vo-heard is-hint">{PHASE_TEXT[s.phase] || ' '}</div>}
        {s.reply ? <div className="vo-reply">{s.reply}</div> : null}
        {s.error ? <div className="vo-reply is-error">{s.error}</div> : null}
      </div>
    </div>
  );
}

/* ---------------- timer cards ---------------- */

function TimerCard({ t, now, onOpen, onCancel }) {
  if (t.source === 'alexa') return <AlexaTimerCard t={t} now={now} />;
  const paused = t.state === 'paused';
  const left = timerLeft(t, now);
  const share = t.seconds > 0 ? Math.max(0, Math.min(1, left / t.seconds)) : 0;
  return (
    <div className={'vo-timer' + (paused ? ' is-paused' : '')} role="status" aria-label={t.name}>
      <button type="button" className="vo-timer-main is-open" onClick={onOpen}>
        <span className="vo-timer-name">{t.name.charAt(0).toUpperCase() + t.name.slice(1)}</span>
        <span className="vo-timer-time">{countdown(left)}</span>
        {paused ? <span className="vo-timer-where">Paused</span> : null}
      </button>
      <span className="vo-timer-btns">
        <button type="button" className="vo-timer-x" onClick={() => timerApi.change(t, { do: paused ? 'resume' : 'pause' })}
          aria-label={paused ? `Resume the ${t.name}` : `Pause the ${t.name}`}>
          <Icon name={paused ? 'play' : 'pause'} size={16} strokeWidth={2.4} />
        </button>
        <button type="button" className="vo-timer-x" onClick={onCancel} aria-label={`Cancel the ${t.name}`}>
          <Icon name="x" size={18} strokeWidth={2.4} />
        </button>
      </span>
      <span className="vo-timer-bar" style={{ transform: `scaleX(${share.toFixed(4)})` }} />
    </div>
  );
}

/** One of the Echos' timers: counts down, then "Done" (it rings on the Echo, not here; no tap to stop). */
function AlexaTimerCard({ t, now }) {
  const left = Math.max(0, t.endsAt - now);
  const done = left <= 0;
  const share = t.seconds > 0 ? Math.max(0, Math.min(1, left / t.seconds)) : 0;
  return (
    <div className={'vo-timer vo-timer--alexa' + (done ? ' is-done' : '')} role="status" aria-label={t.name}>
      <div className="vo-timer-main">
        <span className="vo-timer-name">{t.name}</span>
        <span className="vo-timer-time">{done ? 'Done' : countdown(left)}</span>
        <span className="vo-timer-where">{done ? 'Ringing on ' + t.where : t.where}</span>
      </div>
      <span className="vo-timer-bar" style={{ transform: `scaleX(${share.toFixed(4)})` }} />
    </div>
  );
}

/** A list the hub keeps for this tablet (`path`, pushed as {type: `push`, now, [push]: [...]}), with the hub's clock. */
function useHubList(path, push, key) {
  const [list, setList] = useState([]);
  const skew = useRef(0);
  const load = useCallback(async () => {
    try {
      const r = await api.get(path, { quiet: true });
      skew.current = r.now - Date.now() / 1000;
      setList(r[key] || []);
    } catch {
      /* an older hub, or offline: none */
    }
  }, [path, key]);
  useEffect(() => {
    load();
  }, [load]);
  useLive((m) => {
    if (m.type === push) {
      skew.current = m.now - Date.now() / 1000;
      setList(m[key] || []);
    }
    if (m.type === 'reconnected') load();
  });
  return { list, skew, setList };
}

/** Redraws every `ms` while `on`; returns the hub's clock now (seconds). */
function useTick(on, skew, ms = 250) {
  const [, tick] = useState(0);
  useEffect(() => {
    if (!on) return undefined;
    const t = setInterval(() => tick((x) => x + 1), ms);
    return () => clearInterval(t);
  }, [on, ms]);
  return Date.now() / 1000 + skew.current;
}

/**
 * The sound while something rings: the app's alarm sound (ring / stopRing), else beeps here. A timer
 * says "Your … timer is done" once first. Quiet while someone talks to Newman (`hold`: "Hey Newman, stop").
 */
function useRingSound(ring, hold, ringMs, volume) {
  const key = ring ? ring.kind + ':' + ring.id : '';
  const spoken = useRef({});
  useEffect(() => {
    if (!ring || hold) return undefined;
    let cancelled = false;
    let stopBeeps = () => {};
    let speech = null;
    const n = native();
    const start = () => {
      if (cancelled) return;
      if (n && typeof n.ring === 'function') {
        n.ring(JSON.stringify({ kind: ring.kind, id: ring.id, title: ring.item.name, ringMs }));
      } else {
        if (n && n.wake) n.wake();
        stopBeeps = beeps();
      }
    };
    (async () => {
      if (ring.kind === 'timer' && !spoken.current[ring.id]) {
        spoken.current[ring.id] = true;
        let a = null;
        try {
          a = (await api.get(`api/voice/timers/${enc(ring.id)}/done`, { quiet: true })).audio;
        } catch {
          a = null;
        }
        if (cancelled) return;
        speech = play(a, volume);
        await Promise.race([speech.done, new Promise((r) => setTimeout(r, 4000))]);
      }
      start();
    })();
    return () => {
      cancelled = true;
      if (speech) speech.stop();
      stopBeeps();
      if (n && typeof n.stopRing === 'function') n.stopRing();
    };
  }, [key, hold, ringMs]); // eslint-disable-line react-hooks/exhaustive-deps
}

/* ---------------- the layer ---------------- */

export default function VoiceLayer({ homePage, onWake }) {
  const info = useApi('api/voice');
  const [s, setS] = useState({ phase: 'idle', level: 0, heard: '', reply: '', error: '' });
  const [person, setPerson] = useState(null);
  const people = useApi(person ? 'api/people' : null);
  const timers = useHubList('api/voice/timers', 'timers', 'timers');
  const alarms = useHubList('api/voice/alarms', 'alarms', 'alarms');
  const echoTimers = useHubList('api/alexa/timers', 'alexaTimers', 'timers');
  // The open sheet: {type: 'list'} | {type: 'timer', id} | {type: 'newTimer'} | {type: 'alarm', alarm}.
  const [sheet, setSheet] = useState(null);
  const speech = useRef(null);
  const hideTimer = useRef(null);
  const settings = (info.data && info.data.settings) || {};
  const volume = settings.volume;
  const phrase = (info.data && info.data.wakeword && info.data.wakeword.phrase) || 'Hey Newman';
  const phraseRef = useRef(phrase);
  phraseRef.current = phrase;
  const [ringing, setRinging] = useState(() => nativeRinging() || DEFAULT_RINGING);

  // "Teach Hey Newman", started from the dashboard (TeachVoice.jsx); its clip events go to clipSink.
  const [teach, setTeach] = useState(null);
  const clipSink = useRef(null);

  // Tell the wall app whether to listen, and how keenly (not while teaching).
  useEffect(() => {
    const n = native();
    const d = info.data;
    if (!n || !n.voiceConfig || !d) return;
    const on = !!(!teach && d.available && d.settings && d.settings.enabled && d.wakeword);
    n.voiceConfig(JSON.stringify({ enabled: on, threshold: d.wakeword ? d.wakeword.threshold : 0.5, version: d.wakeword ? d.wakeword.version : '' }));
  }, [info.data, teach]);
  useLive((m) => {
    if (m.type === 'voice-settings' || m.type === 'reconnected') info.reload();
    if (m.type === 'voice-teach' && m.person) {
      setS((x) => ({ ...x, phase: 'idle' }));
      const n = native();
      if (n && n.wake) n.wake();
      setTeach({ person: String(m.person), slug: m.slug || '', target: Number(m.target) || 30, at: Date.now() });
    }
    if (m.type === 'voice-teach-stop') setTeach(null);
  });

  // The app's "Snooze for" / "Stop ringing after" to the hub (it needs them for "snooze" and to stop ringing).
  const [mic, setMic] = useState(micAllowed);
  const hubRinging = info.data && info.data.ringing;
  useEffect(() => {
    const sync = () => {
      setMic(micAllowed());
      const mine = nativeRinging();
      if (!mine) {
        if (hubRinging) setRinging(hubRinging);
        return;
      }
      setRinging(mine);
      if (hubRinging && (hubRinging.snoozeMinutes !== mine.snoozeMinutes || hubRinging.ringMinutes !== mine.ringMinutes)) {
        api.put('api/voice/ringing', mine, { quiet: true }).then(() => info.reload(), () => {});
      }
    };
    sync();
    window.addEventListener('newmanwall-resume', sync);
    return () => window.removeEventListener('newmanwall-resume', sync);
  }, [hubRinging && hubRinging.snoozeMinutes, hubRinging && hubRinging.ringMinutes]); // eslint-disable-line react-hooks/exhaustive-deps

  // Why it can't listen: speech not available on the hub (a plain line), or the microphone not allowed.
  const d = info.data;
  const voiceOn = !!(d && d.settings && d.settings.enabled && !teach);
  let problem = null;
  if (voiceOn && (!d.available || !d.wakeword)) problem = { kind: 'hub', phrase, reason: d.reason || "the speech containers aren't running" };
  else if (voiceOn && mic === false) problem = { kind: 'mic', phrase };
  // …and as soon as it can, it listens by itself: asked again every RECHECK_MS while it can't.
  useEffect(() => {
    if (!problem) return undefined;
    const t = setInterval(() => {
      if (problem.kind === 'hub') info.reload();
      setMic(micAllowed());
    }, RECHECK_MS);
    return () => clearInterval(t);
  }, [problem && problem.kind]); // eslint-disable-line react-hooks/exhaustive-deps
  const allowMic = () => {
    const n = native();
    if (n && typeof n.openMicSettings === 'function') n.openMicSettings();
  };

  const done = useCallback((listen = false) => {
    const n = native();
    if (n && n.voiceDone) n.voiceDone(listen);
  }, []);

  const hideSoon = useCallback((ms = HIDE_AFTER_MS) => {
    clearTimeout(hideTimer.current);
    hideTimer.current = setTimeout(() => setS((x) => ({ ...x, phase: 'idle' })), ms);
  }, []);

  const stopAll = useCallback(() => {
    if (speech.current) speech.current.stop();
  }, []);

  const act = useCallback(
    (action) => {
      if (!action) return;
      if (action.type === 'stop') stopAll();
      if (action.show === 'alarms') {
        setSheet({ type: 'list' });
        return;
      }
      if (action.type !== 'show') return;
      if (action.show === 'lyrics') {
        openSheet({ lyrics: true }); // the player sheet, with the words
        return;
      }
      const to = {
        camera: action.entityId ? 'wall/camera/' + enc(action.entityId) : null,
        cameras: 'wall/cameras',
        page: action.pageId ? 'wall/' + enc(action.pageId) : null,
        home: 'wall/' + (homePage ? enc(homePage) : ''),
        weather: 'wall/weather',
        calendar: 'wall/calendar',
        todo: 'wall/todo',
      }[action.show];
      if (action.show === 'person' && action.personId) setPerson(action.personId);
      else if (to) {
        setPerson(null);
        navigate(to);
      }
    },
    [homePage, stopAll],
  );

  const ringRef = useRef(false);
  const answer = useCallback(
    async (body, type) => {
      clearTimeout(hideTimer.current);
      setS((x) => ({ ...x, phase: 'thinking', level: 0 }));
      try {
        const r = await sendUtterance(body, type);
        if (!r.heard && !r.reply) {
          setS((x) => ({ ...x, phase: 'idle' }));
          done();
          return r;
        }
        setS({ phase: r.audio ? 'speaking' : 'done', level: 0, heard: r.heard, reply: r.reply, error: '' });
        act(r.action);
        if (r.audio) {
          // Quieter over something ringing (its sound stops while Newman listens, and starts again after).
          speech.current = play(r.audio, ringRef.current ? Math.round((volume ?? 80) * 0.4) : volume);
          await speech.current.done;
          speech.current = null;
          setS((x) => (x.phase === 'speaking' ? { ...x, phase: 'done' } : x));
        }
        const listen = !!(r.action && r.action.listen);
        done(listen);
        if (!listen) hideSoon();
        return r;
      } catch (e) {
        setS((x) => ({ ...x, phase: 'error', error: e.message || 'Something went wrong.' }));
        done();
        hideSoon();
        return null;
      }
    },
    [act, done, hideSoon, volume],
  );

  // The wall app's events.
  useEffect(() => {
    const handler = {
      event(e) {
        if (!e || typeof e !== 'object') return;
        if (typeof e.type === 'string' && (e.type === 'clip' || e.type.startsWith('clip-'))) {
          if (clipSink.current) clipSink.current(e);
          return;
        }
        if (e.type === 'wake') {
          clearTimeout(hideTimer.current);
          stopAll();
          // (After a question the card keeps the question showing while it listens for the answer.)
          setS((x) => (e.followUp ? { ...x, phase: 'listening', level: 0, error: '' } : { phase: 'listening', level: 0, heard: '', reply: '', error: '' }));
        } else if (e.type === 'level') {
          setS((x) => (x.phase === 'listening' ? { ...x, level: Number(e.value) || 0 } : x));
        } else if (e.type === 'audio' && e.pcm) {
          answer(b64ToBytes(e.pcm), 'audio/L16; rate=' + (e.rate || 16000));
        } else if (e.type === 'text' && e.text) {
          answer(JSON.stringify({ text: String(e.text) }), 'application/json');
        } else if (e.type === 'cancel') {
          done();
          if (e.quiet) {
            setS((x) => ({ ...x, phase: 'done' }));
          } else {
            setS((x) => ({ ...x, phase: 'error', error: `I didn't catch that - say ${phraseRef.current} and try again.` }));
          }
          hideSoon();
        } else if (e.type === 'error') {
          setS((x) => ({ ...x, phase: 'error', error: e.message || "The microphone isn't available." }));
          hideSoon();
        }
      },
    };
    window.NewmanVoice = handler;
    return () => {
      if (window.NewmanVoice === handler) delete window.NewmanVoice;
    };
  }, [answer, done, hideSoon, stopAll]);

  useEffect(() => () => clearTimeout(hideTimer.current), []);

  // Opened by the big clock at the top (WallApp's TopBar sends 'newman-alarms').
  useEffect(() => {
    const open = () => setSheet({ type: 'list' });
    window.addEventListener('newman-alarms', open);
    return () => window.removeEventListener('newman-alarms', open);
  }, []);

  // What rings, and what rings here because the hub didn't say (it's away).
  const [localDone, setLocalDone] = useState(() => new Set());
  const anyLive = timers.list.length > 0 || alarms.list.length > 0 || echoTimers.list.length > 0 || !!sheet;
  // Countdowns redraw 4 times a second; with only alarms set, once a second is enough.
  const fast = timers.list.length > 0 || echoTimers.list.length > 0 || !!sheet || alarms.list.some((a) => a.state !== 'set');
  const now = useTick(anyLive, timers.skew, fast ? 250 : 1000);
  const ringFor = ringing.ringMinutes * 60;
  const local = localRings(alarms.list, Date.now() / 1000 + alarms.skew.current, ringFor, localDone);
  const rings = ringingNow(timers.list, alarms.list, local);
  const ring = rings[0] || null;
  ringRef.current = !!ring;
  const talking = s.phase === 'listening' || s.phase === 'thinking';
  useRingSound(ring, talking, ringFor * 1000, volume);
  const ringKey = ring ? ring.kind + ':' + ring.id : '';
  useEffect(() => {
    if (ring && onWake) onWake(); // the wall's own dimmed screen goes too
  }, [ringKey]); // eslint-disable-line react-hooks/exhaustive-deps
  const doneLocally = (a) => setLocalDone((x) => new Set([...x, `${a.id}@${a.state === 'snoozed' ? a.snoozeUntil : a.nextAt}`]));

  // The coming alarms, for the app to ring one itself if this page isn't there (times in the tablet's clock).
  const schedKey = JSON.stringify(alarmSchedule(alarms.list));
  useEffect(() => {
    const n = native();
    if (!n || typeof n.alarmSchedule !== 'function') return;
    const list = JSON.parse(schedKey).map((x) => ({ ...x, at: Math.round((x.at - alarms.skew.current) * 1000) }));
    n.alarmSchedule(JSON.stringify(list));
  }, [schedKey]); // eslint-disable-line react-hooks/exhaustive-deps

  // Music goes quiet while Newman listens or speaks, and while something rings (music.js).
  const speaking = talking || s.phase === 'speaking';
  useEffect(() => duck('voice', speaking), [speaking]);
  useEffect(() => duck('timer', !!ring), [!!ring]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => setVoiceCard(s.phase !== 'idle' || !!ring || !!sheet), [s.phase, !!ring, !!sheet]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => (duck('voice', false), duck('timer', false), setVoiceCard(false)), []);

  const close = () => {
    stopAll();
    clearTimeout(hideTimer.current);
    setS((x) => ({ ...x, phase: 'idle' }));
    done();
  };
  const cancelTimer = (t) => {
    timers.setList((l) => l.filter((x) => x.id !== t.id));
    timerApi.cancel(t);
  };
  const stopRing = () => {
    if (!ring) return;
    if (ring.kind === 'timer') {
      cancelTimer(ring.item);
      return;
    }
    if (ring.local) doneLocally(ring.item);
    alarms.setList((l) => l.map((a) => (a.id === ring.id ? { ...a, state: 'set', enabled: a.days.length ? a.enabled : false } : a)));
    alarmApi.stop(ring.item);
  };
  const snoozeRing = () => {
    if (!ring || ring.kind !== 'alarm') return;
    if (ring.local) doneLocally(ring.item);
    alarms.setList((l) => l.map((a) => (a.id === ring.id ? { ...a, state: 'snoozed', snoozeUntil: now + ringing.snoozeMinutes * 60 } : a)));
    alarmApi.snooze(ring.item, ringing.snoozeMinutes);
  };
  const moreRing = () => {
    if (!ring || ring.kind !== 'timer') return;
    timers.setList((l) => l.map((t) => (t.id === ring.id ? { ...t, state: 'running', endsAt: now + 60 } : t)));
    timerApi.change(ring.item, { do: 'add', seconds: 60 });
  };

  const cards = alarmCards(alarms.list, now);
  const shownTimers = timers.list.filter((t) => t.state !== 'ringing');
  const sheetTimer = sheet && sheet.type === 'timer' ? timers.list.find((t) => t.id === sheet.id) : null;
  const shownPerson = person && (people.data || []).find((p) => p.id === person);
  return (
    <>
      {cards.length || shownTimers.length || echoTimers.list.length ? (
        <div className="vo-timers">
          {cards.map((a) => (
            <AlarmCard key={a.id} a={a} now={now} onOpen={() => setSheet({ type: 'list' })} onStop={() => alarmApi.stop(a)} />
          ))}
          {shownTimers.map((t) => (
            <TimerCard key={t.id} t={t} now={now} onOpen={() => setSheet({ type: 'timer', id: t.id })} onCancel={() => cancelTimer(t)} />
          ))}
          {echoTimers.list.map((t) => (
            <TimerCard key={t.id} t={t} now={Date.now() / 1000 + echoTimers.skew.current} />
          ))}
        </div>
      ) : null}
      {problem ? <VoiceProblem problem={problem} onAllow={allowMic} /> : null}
      {sheet && sheet.type === 'list' ? (
        <AlarmsSheet timers={timers.list} alarms={alarms.list} now={now} onClose={() => setSheet(null)}
          onOpenTimer={(t) => setSheet({ type: 'timer', id: t.id })} onNewTimer={() => setSheet({ type: 'newTimer' })}
          onEditAlarm={(a) => setSheet({ type: 'alarm', alarm: a })} />
      ) : null}
      {sheetTimer ? <TimerSheet t={sheetTimer} now={now} onClose={() => setSheet(null)} /> : null}
      {sheet && sheet.type === 'newTimer' ? <NewTimerSheet onClose={() => setSheet(null)} /> : null}
      {sheet && sheet.type === 'alarm' ? <AlarmEditSheet key={sheet.alarm ? sheet.alarm.id : 'new'} a={sheet.alarm} onClose={() => setSheet(null)} /> : null}
      {ring ? <RingScreen ring={ring} snoozeMinutes={ringing.snoozeMinutes} phrase={phrase} onSnooze={snoozeRing} onMore={moreRing} onStop={stopRing} /> : null}
      <VoiceCard s={s} over={!!ring} onClose={close} />
      {teach ? <TeachVoice key={teach.at} session={teach} sink={clipSink} onClose={() => setTeach(null)} /> : null}
      {shownPerson ? <PersonMap person={shownPerson} dark={false} onClose={() => setPerson(null)} /> : null}
    </>
  );
}
