// Voice on the wall tablet: the listening card at the bottom, timer cards, and timer alarms.
//
// The wall app (Android) listens for its wake word ("Hey Newman" unless the tablet's Voice settings
// pick another; api/voice -> wakeword.phrase) on the tablet itself and talks to this page:
//   window.NewmanVoice.event({type: 'wake'})                     the wake word was heard (it chimed)
//   window.NewmanVoice.event({type: 'level', value: 0..1})       how loud it is (the animation)
//   window.NewmanVoice.event({type: 'audio', pcm: base64, rate}) what was said (16-bit mono PCM)
//   window.NewmanVoice.event({type: 'cancel'})                   nothing was said after the wake word
// and this page answers through window.NewmanWall: voiceConfig(json) (on/off, threshold, version - a
// new one, e.g. another wake word chosen, makes the app fetch api/voice/wakeword again), voiceDone()
// (the reply finished - listen for the wake word again). The page sends the audio to the hub
// (POST api/voice/utterance), shows what was heard and the reply, speaks it and does the action
// (a camera full screen, a page, a timer…). Timers live on the hub per tablet: GET api/voice/timers
// and {type: "timers"} pushes; one that ends rings (beeps + "Your pasta timer is done") until a tap
// or "Hey Newman, stop". While the card or a ringing timer shows, the wall counts as in use.
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
import { alarm } from './sounds.js';
import { PersonMap } from './PeopleTile.jsx';
import { countdown, nextAlarmStep, RING_CAP_MS } from './voiceTimers.js';
import TeachVoice from './TeachVoice.jsx';
import { duck, setVoiceCard, openSheet } from './music.js';
import './voice.css';

const HIDE_AFTER_MS = 4500;

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

function VoiceCard({ s, onClose }) {
  if (s.phase === 'idle') return null;
  return (
    <div className={'vo-card is-' + s.phase} role="dialog" aria-label="Newman is listening" onPointerDown={onClose}>
      <Orb phase={s.phase} level={s.level} />
      <div className="vo-text">
        {s.heard ? <div className="vo-heard">“{s.heard}”</div> : <div className="vo-heard is-hint">{PHASE_TEXT[s.phase] || ' '}</div>}
        {s.reply ? <div className="vo-reply">{s.reply}</div> : null}
        {s.error ? <div className="vo-reply is-error">{s.error}</div> : null}
      </div>
    </div>
  );
}

/* ---------------- timers ---------------- */

function TimerCard({ t, now, onStop }) {
  if (t.source === 'alexa') return <AlexaTimerCard t={t} now={now} />;
  const ringing = t.state === 'ringing';
  const left = Math.max(0, t.endsAt - now);
  const share = t.seconds > 0 ? Math.max(0, Math.min(1, left / t.seconds)) : 0;
  return (
    <div className={'vo-timer' + (ringing ? ' is-ringing' : '')} role={ringing ? 'alertdialog' : 'status'} aria-label={t.name}>
      <button type="button" className="vo-timer-main" onClick={ringing ? onStop : undefined} tabIndex={ringing ? 0 : -1}>
        <span className="vo-timer-name">{t.name.charAt(0).toUpperCase() + t.name.slice(1)}</span>
        <span className="vo-timer-time">{ringing ? 'Done' : countdown(left)}</span>
        {ringing ? <span className="vo-timer-hint">Tap to stop</span> : null}
      </button>
      {!ringing ? (
        <button type="button" className="vo-timer-x" onClick={onStop} aria-label={`Cancel the ${t.name}`}>
          <Icon name="x" size={18} strokeWidth={2.4} />
        </button>
      ) : null}
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

/** The Echos' timers (Amazon Alexa connections), with the hub's clock. */
function useAlexaTimers() {
  const [list, setList] = useState([]);
  const skew = useRef(0);
  const load = useCallback(async () => {
    try {
      const r = await api.get('api/alexa/timers', { quiet: true });
      skew.current = r.now - Date.now() / 1000;
      setList(r.timers || []);
    } catch {
      /* an older hub, or offline: none */
    }
  }, []);
  useEffect(() => {
    load();
  }, [load]);
  useLive((m) => {
    if (m.type === 'alexaTimers') {
      skew.current = m.now - Date.now() / 1000;
      setList(m.timers || []);
    }
    if (m.type === 'reconnected') load();
  });
  const [, tick] = useState(0);
  useEffect(() => {
    if (!list.length) return undefined;
    const t = setInterval(() => tick((x) => x + 1), 250);
    return () => clearInterval(t);
  }, [list.length]);
  return { list, now: Date.now() / 1000 + skew.current };
}

/** The hub's timers for this tablet, with its clock (so a tablet whose clock is off counts right). */
function useTimers() {
  const [list, setList] = useState([]);
  const skew = useRef(0);
  const load = useCallback(async () => {
    try {
      const r = await api.get('api/voice/timers', { quiet: true });
      skew.current = r.now - Date.now() / 1000;
      setList(r.timers || []);
    } catch {
      /* an older hub, or offline: no timers */
    }
  }, []);
  useEffect(() => {
    load();
  }, [load]);
  useLive((m) => {
    if (m.type === 'timers') {
      skew.current = m.now - Date.now() / 1000;
      setList(m.timers || []);
    }
    if (m.type === 'reconnected') load();
  });
  const [, tick] = useState(0);
  useEffect(() => {
    if (!list.length) return undefined;
    const t = setInterval(() => tick((x) => x + 1), 250);
    return () => clearInterval(t);
  }, [list.length]);
  return { list, now: Date.now() / 1000 + skew.current, setList };
}

/** Beeps and "Your pasta timer is done", again and again, while a timer rings (about 10 minutes at most). */
function useAlarm(ringing, volume, stopSpeech, paused) {
  const ids = ringing.map((t) => t.id).join(',');
  const spoken = useRef({});
  const hold = useRef(paused);
  hold.current = paused; // quiet while someone talks to Newman ("Hey Newman, stop")
  useEffect(() => {
    if (!ringing.length) return undefined;
    let cancelled = false;
    let stopBeeps = () => {};
    let speech = null;
    let timer = null;
    const started = Date.now();
    let step = 'beep';
    const next = async () => {
      if (cancelled) return;
      if (Date.now() - started > RING_CAP_MS) return; // still shows Done; just quiet now
      if (hold.current) {
        timer = setTimeout(next, 400);
        return;
      }
      const s = nextAlarmStep(step);
      if (step === 'beep') {
        stopBeeps = alarm();
        timer = setTimeout(() => {
          stopBeeps();
          step = s.next;
          next();
        }, s.ms);
      } else if (step === 'speak') {
        const t = ringing[0];
        let a = spoken.current[t.id];
        if (a === undefined) {
          try {
            a = (await api.get(`api/voice/timers/${enc(t.id)}/done`, { quiet: true })).audio;
          } catch {
            a = null;
          }
          spoken.current[t.id] = a;
        }
        if (cancelled) return;
        speech = play(a, volume);
        await speech.done;
        step = s.next;
        timer = setTimeout(next, s.ms);
      }
    };
    next();
    stopSpeech.current = () => {
      if (speech) speech.stop();
    };
    return () => {
      cancelled = true;
      stopBeeps();
      if (speech) speech.stop();
      clearTimeout(timer);
    };
  }, [ids]); // eslint-disable-line react-hooks/exhaustive-deps
}

/* ---------------- the layer ---------------- */

export default function VoiceLayer({ homePage }) {
  const info = useApi('api/voice');
  const [s, setS] = useState({ phase: 'idle', level: 0, heard: '', reply: '', error: '' });
  const [person, setPerson] = useState(null);
  const people = useApi(person ? 'api/people' : null);
  const timers = useTimers();
  const echoTimers = useAlexaTimers();
  const speech = useRef(null);
  const alarmSpeech = useRef(() => {});
  const hideTimer = useRef(null);
  const settings = (info.data && info.data.settings) || {};
  const volume = settings.volume;
  const phrase = (info.data && info.data.wakeword && info.data.wakeword.phrase) || 'Hey Newman';
  const phraseRef = useRef(phrase);
  phraseRef.current = phrase;

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

  const done = useCallback(() => {
    const n = native();
    if (n && n.voiceDone) n.voiceDone();
  }, []);

  const hideSoon = useCallback((ms = HIDE_AFTER_MS) => {
    clearTimeout(hideTimer.current);
    hideTimer.current = setTimeout(() => setS((x) => ({ ...x, phase: 'idle' })), ms);
  }, []);

  const stopAll = useCallback(() => {
    if (speech.current) speech.current.stop();
    alarmSpeech.current();
  }, []);

  const act = useCallback(
    (action) => {
      if (!action) return;
      if (action.type === 'stop') stopAll();
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
          speech.current = play(r.audio, volume);
          await speech.current.done;
          speech.current = null;
          setS((x) => (x.phase === 'speaking' ? { ...x, phase: 'done' } : x));
        }
        done();
        hideSoon();
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
          setS({ phase: 'listening', level: 0, heard: '', reply: '', error: '' });
        } else if (e.type === 'level') {
          setS((x) => (x.phase === 'listening' ? { ...x, level: Number(e.value) || 0 } : x));
        } else if (e.type === 'audio' && e.pcm) {
          answer(b64ToBytes(e.pcm), 'audio/L16; rate=' + (e.rate || 16000));
        } else if (e.type === 'text' && e.text) {
          answer(JSON.stringify({ text: String(e.text) }), 'application/json');
        } else if (e.type === 'cancel') {
          done();
          setS((x) => ({ ...x, phase: 'error', error: `I didn't catch that - say ${phraseRef.current} and try again.` }));
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

  const ringing = timers.list.filter((t) => t.state === 'ringing');
  useAlarm(ringing, volume, alarmSpeech, s.phase === 'listening' || s.phase === 'thinking');

  // Music goes quiet while Newman listens or speaks, and while a timer rings (music.js).
  const talking = s.phase === 'listening' || s.phase === 'thinking' || s.phase === 'speaking';
  useEffect(() => duck('voice', talking), [talking]);
  useEffect(() => duck('timer', ringing.length > 0), [ringing.length]);
  useEffect(() => setVoiceCard(s.phase !== 'idle'), [s.phase]);
  useEffect(() => () => (duck('voice', false), duck('timer', false), setVoiceCard(false)), []);

  const close = () => {
    stopAll();
    clearTimeout(hideTimer.current);
    setS((x) => ({ ...x, phase: 'idle' }));
    done();
  };
  const stopTimer = async (t) => {
    timers.setList((l) => l.filter((x) => x.id !== t.id));
    try {
      await api.del('api/voice/timers/' + enc(t.id), { quiet: true });
    } catch {
      /* gone already */
    }
  };

  const shownPerson = person && (people.data || []).find((p) => p.id === person);
  return (
    <>
      {timers.list.length || echoTimers.list.length ? (
        <div className="vo-timers">
          {timers.list.map((t) => (
            <TimerCard key={t.id} t={t} now={timers.now} onStop={() => stopTimer(t)} />
          ))}
          {echoTimers.list.map((t) => (
            <TimerCard key={t.id} t={t} now={echoTimers.now} />
          ))}
        </div>
      ) : null}
      <VoiceCard s={s} onClose={close} />
      {teach ? <TeachVoice key={teach.at} session={teach} sink={clipSink} onClose={() => setTeach(null)} /> : null}
      {shownPerson ? <PersonMap person={shownPerson} dark={false} onClose={() => setPerson(null)} /> : null}
    </>
  );
}
