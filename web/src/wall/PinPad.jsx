// Sensitive commands on the wall tablet (unlock, open, disarm) follow the hub's `tabletSensitive`
// setting: "none" sends them straight away (the default: it's inside the home), "confirm" asks
// "Unlock Side Door?" first, "pin" asks for the person's PIN. useWallCommand().run(entity, command, value)
// handles it; `mode` says which, so buttons only show a PIN tag when a PIN is asked for.

import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { api, enc, toast } from '../api.js';
import { useHub } from '../hooks.js';
import { valueText } from '../model.js';

const CommandContext = createContext(null);

export function useWallCommand() {
  return useContext(CommandContext);
}

function PinPad({ title, onSubmit, onCancel }) {
  const [pin, setPin] = useState('');
  const [error, setError] = useState('');
  const [shake, setShake] = useState(false);
  const [busy, setBusy] = useState(false);
  const submit = useCallback(async () => {
    if (pin.length < 4 || busy) return;
    setBusy(true);
    const res = await onSubmit(pin);
    setBusy(false);
    if (res && res.error) {
      setError(res.error);
      setPin('');
      if (res.wrong) {
        setShake(true);
        setTimeout(() => setShake(false), 450);
      }
    }
  }, [pin, busy, onSubmit]);
  const press = (d) => {
    setError('');
    setPin((p) => (p.length < 8 ? p + d : p));
  };
  const onKeyRef = useRef(null);
  onKeyRef.current = (e) => {
    if (/^\d$/.test(e.key)) press(e.key);
    else if (e.key === 'Backspace') setPin((p) => p.slice(0, -1));
    else if (e.key === 'Enter') submit();
    else if (e.key === 'Escape') onCancel();
  };
  useEffect(() => {
    const on = (e) => onKeyRef.current(e);
    window.addEventListener('keydown', on);
    return () => window.removeEventListener('keydown', on);
  }, []);
  return (
    <div className="wt-overlay" role="dialog" aria-modal="true" aria-label={title}>
      <div className={'wt-pin' + (shake ? ' is-shaking' : '')}>
        <div className="wt-pin-title">{title}</div>
        <div className="wt-pin-sub">Enter your PIN</div>
        <div className="wt-pin-dots" aria-live="polite" aria-label={`${pin.length} digits entered`}>
          {Array.from({ length: Math.max(4, pin.length) }, (_, i) => (
            <span key={i} className={'wt-pin-dot' + (i < pin.length ? ' is-on' : '')} />
          ))}
        </div>
        <div className={'wt-pin-error' + (error ? '' : ' is-empty')} role={error ? 'alert' : undefined}>
          {error || ' '}
        </div>
        <div className="wt-pin-keys">
          {['1', '2', '3', '4', '5', '6', '7', '8', '9'].map((d) => (
            <button key={d} type="button" className="wt-key" onClick={() => press(d)}>
              {d}
            </button>
          ))}
          <button type="button" className="wt-key wt-key--text" onClick={onCancel}>
            Cancel
          </button>
          <button type="button" className="wt-key" onClick={() => press('0')}>
            0
          </button>
          <button type="button" className="wt-key wt-key--text" onClick={() => setPin((p) => p.slice(0, -1))} aria-label="Delete">
            ⌫
          </button>
        </div>
        <button type="button" className="wt-pin-ok" disabled={pin.length < 4 || busy} onClick={submit}>
          {busy ? 'Checking…' : 'OK'}
        </button>
      </div>
    </div>
  );
}

/** "Unlock Side Door?" with big Cancel / Unlock buttons: one extra tap against accidental touches. */
function ConfirmCommand({ title, label, onOk, onCancel }) {
  const okRef = useRef(null);
  useEffect(() => {
    const on = (e) => e.key === 'Escape' && onCancel();
    window.addEventListener('keydown', on);
    if (okRef.current) okRef.current.focus();
    return () => window.removeEventListener('keydown', on);
  }, [onCancel]);
  return (
    <div className="wt-overlay" role="alertdialog" aria-modal="true" aria-label={title} onClick={(e) => e.target === e.currentTarget && onCancel()}>
      <div className="wt-confirm">
        <div className="wt-pin-title">{title}</div>
        <div className="wt-confirm-btns">
          <button type="button" className="wt-confirm-btn" onClick={onCancel}>
            Cancel
          </button>
          <button ref={okRef} type="button" className="wt-confirm-btn is-ok" onClick={onOk}>
            {label}
          </button>
        </div>
      </div>
    </div>
  );
}

/** For the layout editor's canvas: the real tiles, but nothing is ever sent. */
export function InertCommands({ children }) {
  return <CommandContext.Provider value={{ run: async () => {}, busy: {}, mode: 'none' }}>{children}</CommandContext.Provider>;
}

/** Sends device commands from the tablet; sensitive ones as `mode` says (none / confirm / pin). */
export function CommandProvider({ children, mode = 'none' }) {
  const { reloadEntities, kindsById } = useHub();
  const [pad, setPad] = useState(null); // { entity, cmd, value }: the PIN pad
  const [ask, setAsk] = useState(null); // { entity, cmd, value }: "Are you sure?"
  const [busy, setBusy] = useState({});

  const send = useCallback(
    async (e, cmd, value, pin) => {
      setBusy((b) => ({ ...b, [e.id]: cmd.id }));
      try {
        await api.post('api/entities/' + enc(e.id) + '/command', { command: cmd.id, ...(value !== undefined ? { value } : {}), ...(pin ? { pin } : {}) }, { quiet: true });
        toast(value === undefined ? `${cmd.label} ${e.name}` : `${e.name}: ${cmd.label.toLowerCase()} ${valueText(cmd, kindsById[e.kind], value)}`, 'success');
        return { ok: true };
      } catch (err) {
        const d = err.data && typeof err.data === 'object' ? err.data : {};
        if (err.status === 403 && d.needsPin) return { error: d.error || 'Wrong PIN', wrong: /wrong/i.test(d.error || ''), needsPin: true };
        if (err.status === 429) return { error: err.message };
        toast(err.message || `${cmd.label} didn't work`, 'error');
        return { ok: false };
      } finally {
        setBusy((b) => ({ ...b, [e.id]: null }));
        reloadEntities();
      }
    },
    [reloadEntities, kindsById],
  );

  // Without a PIN; if the hub still wants one (the setting changed meanwhile), ask for it.
  const sendOrPin = useCallback(
    async (e, cmd, value) => {
      const res = await send(e, cmd, value);
      if (res && res.needsPin) setPad({ entity: e, cmd, value });
      else if (res && res.error) toast(res.error, 'error');
    },
    [send],
  );

  const run = useCallback(
    async (e, cmd, value) => {
      if (cmd.sensitive && mode === 'pin') return setPad({ entity: e, cmd, value });
      if (cmd.sensitive && mode === 'confirm') return setAsk({ entity: e, cmd, value });
      return sendOrPin(e, cmd, value);
    },
    [sendOrPin, mode],
  );

  return (
    <CommandContext.Provider value={{ run, busy, mode }}>
      {children}
      {ask ? (
        <ConfirmCommand
          title={`${ask.cmd.label} ${ask.entity.name}?`}
          label={ask.cmd.label}
          onCancel={() => setAsk(null)}
          onOk={() => {
            const a = ask;
            setAsk(null);
            sendOrPin(a.entity, a.cmd, a.value);
          }}
        />
      ) : null}
      {pad ? (
        <PinPad
          title={`${pad.cmd.label} ${pad.entity.name}`}
          onCancel={() => setPad(null)}
          onSubmit={async (pin) => {
            const res = await send(pad.entity, pad.cmd, pad.value, pin);
            if (res.ok) setPad(null);
            else if (!res.error) setPad(null);
            return res;
          }}
        />
      ) : null}
    </CommandContext.Provider>
  );
}
