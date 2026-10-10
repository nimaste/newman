// "Teach Hey Newman" on the wall tablet: started from the dashboard (a {type: "voice-teach"} push,
// see VoiceLayer.jsx). One calm full-screen card: who's recording, "Say Hey Newman (7 of 30)", a level
// meter and tips; then half a minute of reading a paragraph aloud (ordinary talking, so it learns what
// isn't the wake word). The wall app records each clip with the tablet's microphone:
//   NewmanWall.recordClip(maxMs, fixed)   fixed=false: from the first word to the pause after it;
//                                          fixed=true: maxMs of audio (the talking step)
//   NewmanWall.stopClip()                  stop (a fixed recording hands over what it has; a spoken one is dropped)
// and answers through window.NewmanVoice.event: {type: "clip", pcm, rate, ms} · {type: "clip-level",
// value} · {type: "clip-none"} (nobody spoke) · {type: "clip-error", message}. Each clip goes to the
// hub (POST api/voice/teach/clips). The wake word isn't listened for meanwhile, and the card counts
// as in use (role=dialog) so the wall doesn't go back to its home page.

import { useCallback, useEffect, useRef, useState } from 'react';
import { enc, tabletToken } from '../api.js';

const SAY_MS = 5000;
const TALK_MS = 30000;
const PARAGRAPH =
  'The weather this week looks mostly sunny, with a chance of rain on Thursday afternoon. ' +
  'I need to pick up groceries after work: milk, eggs, bread and something easy for dinner. ' +
  'Did anyone see where I left the car keys? I think they were on the counter next to the mail. ' +
  "Let's call Grandma on Sunday and ask how her garden is doing. " +
  'Practice is at five, so we should eat a little early tonight, and maybe watch a movie after.';

function tip(n, target) {
  const third = Math.max(1, Math.round(target / 3));
  if (n < third) return 'Stand where you usually talk to the tablet and say it in your normal voice.';
  if (n < third * 2) return 'Now mix it up: from across the room, a bit quieter, a bit louder, faster or slower.';
  return 'Last few: with the TV or music on, or from off to the side.';
}

async function upload(person, kind, pcmB64) {
  const bin = atob(pcmB64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i += 1) bytes[i] = bin.charCodeAt(i);
  const tok = tabletToken();
  const res = await fetch(`api/voice/teach/clips?person=${enc(person)}&kind=${kind}`, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'audio/L16; rate=16000', Accept: 'application/json', ...(tok ? { Authorization: 'Bearer ' + tok } : {}) },
    body: bytes,
  });
  const data = await res.json().catch(() => ({}));
  return { ok: res.ok, ...data };
}

async function hubCall(method, path) {
  const tok = tabletToken();
  await fetch(path, { method, credentials: 'same-origin', headers: tok ? { Authorization: 'Bearer ' + tok } : {} }).catch(() => {});
}

export default function TeachVoice({ session, sink, onClose }) {
  const n = window.NewmanWall && window.NewmanWall.native && window.NewmanWall.recordClip ? window.NewmanWall : null;
  const person = session.person;
  const target = session.target || 30;
  const [step, setStep] = useState('say'); // say -> talk -> done
  const [count, setCount] = useState(0);
  const [msg, setMsg] = useState('');
  const [level, setLevel] = useState(0);
  const [phase, setPhase] = useState('idle'); // idle | listening | saving | recording
  const [talkLeft, setTalkLeft] = useState(TALK_MS);
  const ids = useRef([]);
  const live = useRef({ step: 'say', count: 0, stopped: false });
  live.current.step = step;
  live.current.count = count;
  const retry = useRef(null);

  const listen = useCallback(() => {
    if (!n || live.current.stopped) return;
    setPhase('listening');
    n.recordClip(SAY_MS, false);
  }, [n]);

  const later = useCallback((fn, ms) => {
    clearTimeout(retry.current);
    retry.current = setTimeout(fn, ms);
  }, []);

  // The wall app's answers.
  useEffect(() => {
    sink.current = async (e) => {
      if (live.current.stopped) return;
      if (e.type === 'clip-level') {
        setLevel(Number(e.value) || 0);
      } else if (e.type === 'clip-none') {
        setMsg('I didn’t hear anything - say “Hey Newman”.');
        if (live.current.step === 'say') later(listen, 400);
        else setPhase('idle');
      } else if (e.type === 'clip-error') {
        setPhase('idle');
        setMsg(e.message || 'The microphone isn’t available.');
      } else if (e.type === 'clip' && e.pcm) {
        setLevel(0);
        setPhase('saving');
        const kind = live.current.step === 'talk' ? 'negative' : 'positive';
        let r;
        try {
          r = await upload(person, kind, e.pcm);
        } catch {
          r = { ok: false, error: 'Couldn’t reach the hub.' };
        }
        if (live.current.stopped) return;
        if (kind === 'negative') {
          if (r.ok) {
            setStep('done');
            setPhase('idle');
          } else {
            setPhase('idle');
            setMsg(r.error || 'That didn’t save - try again.');
          }
          return;
        }
        if (r.ok) {
          ids.current.push(r.clip.id);
          const c = live.current.count + 1;
          setCount(c);
          setMsg('');
          if (c >= target) {
            setPhase('idle');
            setStep('talk');
            return;
          }
        } else {
          setMsg(r.error || 'That didn’t save - try again.');
        }
        later(listen, 700);
      }
    };
    return () => {
      sink.current = null;
    };
  }, [sink, person, target, listen, later]);

  // Start listening for the first one; let go of the microphone on the way out.
  useEffect(() => {
    later(listen, 600);
    const state = live.current;
    return () => {
      state.stopped = true;
      clearTimeout(retry.current);
      if (n && n.stopClip) n.stopClip();
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // The talking step's countdown.
  useEffect(() => {
    if (phase !== 'recording') return undefined;
    const started = Date.now();
    const t = setInterval(() => setTalkLeft(Math.max(0, TALK_MS - (Date.now() - started))), 250);
    return () => clearInterval(t);
  }, [phase]);

  const finish = () => {
    live.current.stopped = true;
    if (n && n.stopClip) n.stopClip();
    hubCall('POST', 'api/voice/teach/stop');
    onClose();
  };
  const skip = () => {
    clearTimeout(retry.current);
    if (n && n.stopClip) n.stopClip();
    setMsg('');
    setPhase('idle');
    setStep(step === 'say' ? 'talk' : 'done');
  };
  const redo = async () => {
    const id = ids.current.pop();
    if (!id) return;
    setCount((c) => Math.max(0, c - 1));
    setMsg('Removed the last one - say it again.');
    await hubCall('DELETE', `api/voice/teach/${enc(session.slug || '')}/positive/${enc(id)}`);
  };
  const startTalking = () => {
    setMsg('');
    setTalkLeft(TALK_MS);
    setPhase('recording');
    n.recordClip(TALK_MS, true);
  };

  let body;
  if (!n) {
    body = (
      <>
        <div className="vt-title">Teaching needs the Newman Wall app</div>
        <div className="vt-tip">It records with this tablet’s microphone - open the wall in the Newman Wall app.</div>
      </>
    );
  } else if (step === 'say') {
    body = (
      <>
        <div className="vt-who">{person} is recording</div>
        <div className="vt-title">
          Say “Hey Newman” <span className="vt-count">({Math.min(count + 1, target)} of {target})</span>
        </div>
        <div className="vt-meter" aria-hidden="true">
          <span style={{ transform: `scaleX(${(phase === 'listening' ? Math.max(0.02, level) : 0.02).toFixed(3)})` }} />
        </div>
        <div className="vt-state">{phase === 'saving' ? 'Got it…' : phase === 'listening' ? 'Listening…' : ' '}</div>
        <div className="vt-tip">{tip(count, target)}</div>
      </>
    );
  } else if (step === 'talk') {
    body = (
      <>
        <div className="vt-who">{person} is recording</div>
        <div className="vt-title">Now just talk normally for 30 seconds</div>
        <div className="vt-tip">Read this out loud in your everyday voice - it helps Newman learn what isn’t “Hey Newman”.</div>
        <p className="vt-paragraph">{PARAGRAPH}</p>
        {phase === 'recording' ? (
          <>
            <div className="vt-meter" aria-hidden="true">
              <span style={{ transform: `scaleX(${Math.max(0.02, level).toFixed(3)})` }} />
            </div>
            <div className="vt-state">Recording… {Math.ceil(talkLeft / 1000)} s</div>
          </>
        ) : phase === 'saving' ? (
          <div className="vt-state">Saving…</div>
        ) : (
          <button type="button" className="vt-btn vt-btn-main" onClick={startTalking}>
            Start
          </button>
        )}
      </>
    );
  } else {
    body = (
      <>
        <div className="vt-title">Thanks, {person}!</div>
        <div className="vt-tip">
          {count} “Hey Newman” recordings saved on the hub. Someone else can record next (start it from the dashboard). When everyone has, train the new “Hey Newman” from the dashboard: Household → Accounts → this tablet → Voice.
        </div>
      </>
    );
  }

  return (
    <div className="vt-screen" role="dialog" aria-label="Teach Hey Newman">
      <div className="vt-card">
        {body}
        {msg ? <div className="vt-msg">{msg}</div> : null}
        <div className="vt-actions">
          {n && step === 'say' ? (
            <>
              <button type="button" className="vt-btn" onClick={skip}>
                Skip ahead
              </button>
              <button type="button" className="vt-btn" onClick={redo} disabled={!ids.current.length}>
                Redo last
              </button>
            </>
          ) : null}
          {n && step === 'talk' && phase === 'recording' ? (
            <button type="button" className="vt-btn" onClick={() => n.stopClip()}>
              Stop early
            </button>
          ) : null}
          {n && step === 'talk' && phase === 'idle' ? (
            <button type="button" className="vt-btn" onClick={skip}>
              Skip
            </button>
          ) : null}
          <button type="button" className="vt-btn vt-btn-done" onClick={finish}>
            Done
          </button>
        </div>
      </div>
    </div>
  );
}
