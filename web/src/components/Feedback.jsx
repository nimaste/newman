import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { onApiEvent } from '../api.js';
import { Icon } from '../icons.jsx';

/** Toast stack fed by api.js events (errors) and toast() calls. */
export function Toasts() {
  const [items, setItems] = useState([]);
  const nextId = useRef(1);

  const dismiss = useCallback((id) => setItems((xs) => xs.filter((x) => x.id !== id)), []);

  useEffect(
    () =>
      onApiEvent((e) => {
        if (e.type !== 'toast') return;
        const id = nextId.current++;
        setItems((xs) => {
          // Collapse repeats of the same message.
          const rest = xs.filter((x) => x.message !== e.message);
          return [...rest, { id, message: e.message, kind: e.kind }].slice(-4);
        });
        setTimeout(() => dismiss(id), e.kind === 'error' ? 7000 : 4000);
      }),
    [dismiss],
  );

  return (
    <div className="toasts" role="status" aria-live="polite">
      {items.map((t) => (
        <div key={t.id} className={'toast toast--' + t.kind}>
          <span className="grow">{t.message}</span>
          <button type="button" className="icon-btn" aria-label="Dismiss message" onClick={() => dismiss(t.id)}>
            <Icon name="x" size={16} />
          </button>
        </div>
      ))}
    </div>
  );
}

const ConfirmContext = createContext(() => Promise.resolve(false));

/** const confirm = useConfirm(); if (await confirm({title, body, confirmLabel, danger})) … */
export function useConfirm() {
  return useContext(ConfirmContext);
}

export function ConfirmProvider({ children }) {
  const [req, setReq] = useState(null);
  // Destructive dialogs start on Cancel; others on the confirm button.
  const initialFocus = useRef(null);
  const lastFocus = useRef(null);

  const confirm = useCallback(
    (opts) =>
      new Promise((resolve) => {
        lastFocus.current = document.activeElement;
        setReq({ ...opts, resolve });
      }),
    [],
  );

  const close = (result) => {
    if (req) req.resolve(result);
    setReq(null);
    if (lastFocus.current && lastFocus.current.focus) {
      setTimeout(() => lastFocus.current && lastFocus.current.focus(), 0);
    }
  };

  useEffect(() => {
    if (!req) return undefined;
    initialFocus.current && initialFocus.current.focus();
    const onKey = (e) => {
      if (e.key === 'Escape') close(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [req]);

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      {req ? (
        <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && close(false)}>
          <div className="modal" role="alertdialog" aria-modal="true" aria-labelledby="confirm-title" aria-describedby="confirm-body">
            <div id="confirm-title" className="modal-title">
              {req.title}
            </div>
            {req.body ? (
              <div id="confirm-body" className="modal-body">
                {req.body}
              </div>
            ) : null}
            {req.sensitive ? (
              <div className="warn">
                <Icon name="alert" size={16} />
                <span>This is a sensitive action. On a phone it needs Face ID or a fingerprint.</span>
              </div>
            ) : null}
            <div className="modal-actions">
              <button type="button" className="btn" ref={req.danger ? initialFocus : null} onClick={() => close(false)}>
                Cancel
              </button>
              <button
                type="button"
                ref={req.danger ? null : initialFocus}
                className={'btn ' + (req.danger ? 'btn-danger-solid' : 'btn-primary')}
                onClick={() => close(true)}
              >
                {req.confirmLabel || 'OK'}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </ConfirmContext.Provider>
  );
}
