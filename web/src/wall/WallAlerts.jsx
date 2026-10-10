// Alerts an automation shows on the wall tablet ({"type": "wall", "alert"} on the live socket).
// card: slides in at the top, goes away after ~20 s (tap: the camera full screen).
// full: the camera live and full screen, back to the dashboard after 60 s without a touch.
// alarm: full-screen red until OK. Newest on top; a full/alarm takes over; cards stack (max 3).
// Only for this tablet: a tablet session, and `tablets` empty or naming this account.

import { useCallback, useEffect, useRef, useState } from 'react';
import { api, enc, toast } from '../api.js';
import { navigate, useHub } from '../hooks.js';
import { useLive } from '../live.js';
import { Icon } from '../icons.jsx';
import { fmtClock } from '../format.js';
import LiveVideo from '../components/LiveVideo.jsx';
import { useWallCommand } from './PinPad.jsx';
import { playSound, unlockAudioOnFirstTouch } from './sounds.js';
import { duck } from './music.js';

const CARD_MS = 20000;
const FULL_IDLE_MS = 60000;
const MAX_CARDS = 3;

/** Does this alert belong on this tablet? */
export function alertIsForMe(alert, auth) {
  if (!alert || !auth || !auth.session || auth.session.kind !== 'tablet') return false;
  const ids = Array.isArray(alert.tablets) ? alert.tablets : [];
  return !ids.length || (!!auth.user && ids.includes(auth.user.id));
}

/** An alert's buttons: device buttons send the device command (confirm/PIN as usual), others press the run's button. */
function AlertButtons({ alert, onDone, big }) {
  const { entitiesById, kindsById } = useHub();
  const { run } = useWallCommand();
  const [busy, setBusy] = useState('');
  const press = async (b) => {
    if (b.entity && b.command) {
      const e = entitiesById[b.entity];
      if (!e) return toast('That device is missing', 'error');
      const k = kindsById[e.kind];
      const cmd = ((k && k.commands) || []).find((c) => c.id === b.command) || { id: b.command, label: b.label, sensitive: !!b.sensitive };
      await run(e, cmd);
      return undefined;
    }
    setBusy(b.id);
    try {
      await api.post('api/runs/' + enc(alert.id) + '/press', { button: b.id }, { quiet: true });
      onDone(alert);
    } catch (err) {
      toast(err.message || `${b.label} didn't work`, 'error');
    } finally {
      setBusy('');
    }
    return undefined;
  };
  const buttons = Array.isArray(alert.buttons) ? alert.buttons : [];
  if (!buttons.length) return null;
  return (
    <div className={'wa-buttons' + (big ? ' wa-buttons--big' : '')}>
      {buttons.map((b) => (
        <button
          key={b.id}
          type="button"
          className={'wa-btn' + (b.sensitive ? ' is-sensitive' : '')}
          disabled={busy === b.id}
          onClick={(ev) => {
            ev.stopPropagation();
            press(b);
          }}
        >
          {b.icon ? <Icon name={b.icon} size={big ? 20 : 17} /> : null}
          {b.label}
        </button>
      ))}
    </div>
  );
}

function Picture({ id, quality, priority }) {
  const { entitiesById } = useHub();
  const cam = id ? entitiesById[id] : null;
  if (!cam) return null;
  return <LiveVideo key={cam.id + quality} entity={cam} quality={quality} priority={priority} stillHeight={quality === 'main' ? 1080 : 480} showNote={false} className="wa-video" />;
}

function Card({ alert, onClose }) {
  const { entitiesById } = useHub();
  useEffect(() => {
    const t = setTimeout(() => onClose(alert), CARD_MS);
    return () => clearTimeout(t);
  }, [alert, onClose]);
  const cam = alert.camera && entitiesById[alert.camera];
  return (
    <div className="wa-card" role="alert">
      <button
        type="button"
        className="wa-card-main"
        onClick={() => {
          if (!cam) return;
          onClose(alert);
          navigate('wall/camera/' + enc(cam.id));
        }}
        aria-label={cam ? `${alert.title}: watch ${cam.name} full screen` : alert.title}
      >
        {cam ? (
          <span className="wa-card-pic">
            <Picture id={cam.id} quality="sub" />
          </span>
        ) : null}
        <span className="wa-card-text">
          <span className="wa-title">{alert.title}</span>
          {alert.body ? <span className="wa-body">{alert.body}</span> : null}
          <span className="wa-time">{fmtClock(alert.at)}</span>
        </span>
      </button>
      <div className="wa-card-side">
        <AlertButtons alert={alert} onDone={onClose} />
        <button type="button" className="wa-x" onClick={() => onClose(alert)} aria-label="Close">
          <Icon name="x" size={20} strokeWidth={2.4} />
        </button>
      </div>
    </div>
  );
}

function Full({ alert, onClose }) {
  const timer = useRef(null);
  const arm = useCallback(() => {
    clearTimeout(timer.current);
    timer.current = setTimeout(() => onClose(alert), FULL_IDLE_MS);
  }, [alert, onClose]);
  useEffect(() => {
    arm();
    return () => clearTimeout(timer.current);
  }, [arm]);
  return (
    <div className="wa-full" role="alertdialog" aria-modal="true" aria-label={alert.title} onPointerDown={arm}>
      <div className="wa-full-pic">
        {alert.camera ? <Picture id={alert.camera} quality="main" priority /> : <div className="wa-nopic"><Icon name="bell" size={64} /></div>}
      </div>
      <div className="wa-full-bar">
        <div className="wa-full-text">
          <span className="wa-title wa-title--big">{alert.title}</span>
          {alert.body ? <span className="wa-body">{alert.body}</span> : null}
        </div>
        <AlertButtons alert={alert} onDone={onClose} big />
        <button type="button" className="wa-dismiss" onClick={() => onClose(alert)}>
          Dismiss
        </button>
      </div>
    </div>
  );
}

function Alarm({ alert, onClose }) {
  return (
    <div className="wa-alarm" role="alertdialog" aria-modal="true" aria-label={alert.title}>
      <div className="wa-alarm-inner">
        <Icon name="alert" size={88} strokeWidth={2.2} />
        <div className="wa-alarm-title">{alert.title}</div>
        {alert.body ? <div className="wa-alarm-body">{alert.body}</div> : null}
        <AlertButtons alert={alert} onDone={onClose} big />
        <button type="button" className="wa-ok" onClick={() => onClose(alert)}>
          OK
        </button>
      </div>
    </div>
  );
}

/** Listens for wall alerts and shows the ones for this tablet. `onWake` leaves the dimmed screen. */
export default function WallAlerts({ auth, onWake }) {
  const [alerts, setAlerts] = useState([]);
  const stops = useRef({}); // alert id → stop() of its sound
  useEffect(() => {
    unlockAudioOnFirstTouch();
    return () => Object.values(stops.current).forEach((s) => s && s());
  }, []);

  useLive((m) => {
    if (m.type !== 'wall' || !alertIsForMe(m.alert, auth)) return;
    const a = { ...m.alert, key: (m.alert.id || 'a') + ':' + Date.now() };
    if (a.wake !== false) {
      onWake();
      try {
        if (window.NewmanWall && typeof window.NewmanWall.wake === 'function') window.NewmanWall.wake();
      } catch {
        /* the native bridge is optional */
      }
    }
    if (a.sound) stops.current[a.key] = playSound(a.sound);
    setAlerts((list) => [...list.filter((x) => x.id !== a.id || !a.id), a]);
  });

  const close = useCallback((a) => {
    const stop = stops.current[a.key];
    if (stop) stop();
    delete stops.current[a.key];
    setAlerts((list) => list.filter((x) => x.key !== a.key));
  }, []);

  // Music goes quiet while an alert with a sound is up (music.js).
  const sounding = alerts.some((a) => a.sound);
  useEffect(() => duck('alert', sounding), [sounding]);
  useEffect(() => () => duck('alert', false), []);

  // Newest on top: the newest full/alarm takes over; cards stack below it (max 3).
  const takeover = [...alerts].reverse().find((a) => a.style === 'full' || a.style === 'alarm');
  const cards = alerts.filter((a) => a.style !== 'full' && a.style !== 'alarm').reverse().slice(0, MAX_CARDS);

  // Cards beyond the visible ones are let go.
  useEffect(() => {
    const visible = new Set(cards.map((c) => c.key));
    const extra = alerts.filter((a) => a.style !== 'full' && a.style !== 'alarm' && !visible.has(a.key));
    if (extra.length) setAlerts((list) => list.filter((a) => !extra.some((x) => x.key === a.key)));
  }, [alerts]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!alerts.length) return null;
  return (
    <>
      {cards.length && !takeover ? (
        <div className="wa-cards" aria-live="assertive">
          {cards.map((a) => (
            <Card key={a.key} alert={a} onClose={close} />
          ))}
        </div>
      ) : null}
      {takeover ? takeover.style === 'alarm' ? <Alarm key={takeover.key} alert={takeover} onClose={close} /> : <Full key={takeover.key} alert={takeover} onClose={close} /> : null}
    </>
  );
}
