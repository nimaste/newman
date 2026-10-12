// Backups (admins): the nightly backup, saved backups, downloading one (optionally locked with a
// passphrase) and restoring — always check first, then one clear confirm, then wait for the restart.

import { useEffect, useRef, useState } from 'react';
import { api, enc, toast } from '../api.js';
import { useApi } from '../hooks.js';
import { useAuth } from '../auth.jsx';
import { useConfirm } from '../components/Feedback.jsx';
import { Empty, ErrorBox, Loading, Switch } from '../components/Controls.jsx';
import { Icon } from '../icons.jsx';
import { plural } from '../format.js';

const SHOWN = 5;

function toDate(v) {
  if (v === null || v === undefined || v === '') return null;
  if (typeof v === 'number') return new Date(v < 1e12 ? v * 1000 : v);
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? null : d;
}
function fmtDate(v) {
  const d = toDate(v);
  return d ? d.toLocaleString([], { month: 'short', day: 'numeric', year: d.getFullYear() === new Date().getFullYear() ? undefined : 'numeric', hour: 'numeric', minute: '2-digit' }) : '';
}
function fmtSize(n) {
  if (typeof n !== 'number') return '';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}
/** "Backup from Oct 1, 9:43 PM - 1 account, 5 automations, 11 connections, 1 dashboard" */
function contents(b) {
  const c = (b && b.counts) || {};
  const parts = [
    typeof c.users === 'number' ? plural(c.users, 'account') : null,
    typeof c.automations === 'number' ? plural(c.automations, 'automation') : null,
    typeof c.integrations === 'number' ? plural(c.integrations, 'connection') : null,
    typeof c.dashboards === 'number' ? plural(c.dashboards, 'dashboard') : null,
  ].filter(Boolean);
  return `Backup from ${fmtDate(b && b.created) || 'an unknown date'}${parts.length ? ' - ' + parts.join(', ') : ''}`;
}

/** POST that answers with a file: save it under the name the hub gives. */
async function downloadFile(body) {
  const res = await fetch('api/backups/download', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  if (!res.ok) {
    let msg = `Download failed (${res.status})`;
    try {
      msg = (await res.json()).error || msg;
    } catch {
      /* not JSON */
    }
    throw new Error(msg);
  }
  const cd = res.headers.get('Content-Disposition') || '';
  const m = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(cd);
  const name = m ? decodeURIComponent(m[1]) : `newman-backup-${new Date().toISOString().slice(0, 10)}`;
  const blob = await res.blob();
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  return name;
}

/** Restore from a file (multipart), or a saved one (JSON). check=true only reads it. */
async function restoreRequest({ file, name, passphrase, check }) {
  let res;
  if (file) {
    const fd = new FormData();
    fd.append('file', file);
    if (passphrase) fd.append('passphrase', passphrase);
    fd.append('check', check ? 'true' : 'false');
    res = await fetch('api/backups/restore', { method: 'POST', credentials: 'same-origin', body: fd });
  } else {
    res = await fetch('api/backups/restore', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, check }) });
  }
  let data = null;
  try {
    data = await res.json();
  } catch {
    data = null;
  }
  if (!res.ok || !data || data.ok === false) throw new Error((data && data.error) || `Restore failed (${res.status})`);
  return data;
}

/** While the hub restarts: wait, then reload to the sign-in page once /health answers. */
function Restarting() {
  const [tries, setTries] = useState(0);
  useEffect(() => {
    let alive = true;
    let n = 0;
    const tick = async () => {
      if (!alive) return;
      n += 1;
      setTries(n);
      try {
        const r = await fetch('health', { cache: 'no-store' });
        if (r.ok) {
          window.location.hash = '#/';
          window.location.reload();
          return;
        }
      } catch {
        /* still restarting */
      }
      setTimeout(tick, 1500);
    };
    // The hub restarts about a second after answering: don't catch the old one still running.
    const t = setTimeout(tick, 3000);
    return () => {
      alive = false;
      clearTimeout(t);
    };
  }, []);
  return (
    <div className="restart-veil" role="status" aria-live="polite">
      <div className="restart-card">
        <span className="spinner" style={{ width: 28, height: 28 }} />
        <div className="restart-title">Restarting…</div>
        <div className="help">Newman is restoring the backup. This page reloads by itself when it's back{tries > 6 ? ' — still waiting' : ''}.</div>
      </div>
    </div>
  );
}

export default function Backups() {
  const { isAdmin } = useAuth();
  const confirm = useConfirm();
  const res = useApi(isAdmin ? 'api/backups' : null);
  const [settings, setSettings] = useState(null);
  const [showAll, setShowAll] = useState(false);
  const [busy, setBusy] = useState('');
  const [pass, setPass] = useState('');
  const [file, setFile] = useState(null);
  const [filePass, setFilePass] = useState('');
  const [fileError, setFileError] = useState('');
  const [restarting, setRestarting] = useState(false);
  const fileInput = useRef(null);
  const filePassInput = useRef(null);

  useEffect(() => {
    if (res.data) setSettings({ auto: res.data.auto !== false, keep: res.data.keep ?? 7, time: res.data.time || '03:30' });
  }, [res.data]);

  const saveSettings = async (patch) => {
    const next = { ...settings, ...patch };
    setSettings(next);
    try {
      const saved = await api.patch('api/backups/settings', patch);
      setSettings({ auto: saved.auto !== false, keep: saved.keep ?? next.keep, time: saved.time || next.time });
      res.setData((d) => ({ ...d, ...saved }));
    } catch {
      res.reload();
    }
  };

  const passBad = pass && pass.length < 8;
  const download = async (name) => {
    if (passBad) return toast('A passphrase needs at least 8 characters.', 'error');
    setBusy('dl:' + (name || 'new'));
    try {
      const saved = await downloadFile({ ...(name ? { name } : {}), ...(pass ? { passphrase: pass } : {}) });
      toast(`Downloaded ${saved}${pass ? ' (locked)' : ''}`, 'success');
      if (!name) res.reload();
    } catch (e) {
      toast(e.message, 'error');
    } finally {
      setBusy('');
    }
  };
  const backupNow = async () => {
    setBusy('now');
    try {
      await api.post('api/backups', {});
      toast('Backup saved', 'success');
      res.reload();
    } catch {
      /* toast shown */
    } finally {
      setBusy('');
    }
  };
  const remove = async (b) => {
    if (!(await confirm({ title: 'Delete this backup?', body: `${fmtDate(b.created)} · ${fmtSize(b.size)}`, confirmLabel: 'Delete', danger: true }))) return;
    try {
      await api.del('api/backups/' + enc(b.name));
      toast('Backup deleted', 'success');
      res.reload();
    } catch {
      /* toast shown */
    }
  };

  /** Check what's in it, one clear confirm, then restore and wait for the restart. */
  const restore = async (src) => {
    setBusy(src.name ? 'rs:' + src.name : 'file');
    setFileError('');
    let checked;
    try {
      checked = await restoreRequest({ ...src, check: true });
    } catch (e) {
      setBusy('');
      if (src.file) {
        setFileError(e.message);
        if (/passphrase|locked/i.test(e.message) && filePassInput.current) filePassInput.current.focus();
      } else toast(e.message, 'error');
      return;
    }
    setBusy('');
    const ok = await confirm({
      title: 'Restore this backup?',
      body: (
        <>
          <strong>{contents(checked.backup)}</strong>
          <br />
          Restore - this replaces everything in Newman with the backup. What's there now is kept on the server.
        </>
      ),
      confirmLabel: 'Restore',
      danger: true,
    });
    if (!ok) return;
    setBusy('restoring');
    try {
      await restoreRequest({ ...src, check: false });
      setRestarting(true);
    } catch (e) {
      setBusy('');
      toast(e.message, 'error');
    }
  };

  if (!isAdmin) {
    return (
      <div className="app-page">
        <div className="app-head">
          <h1 className="page-title">Backups</h1>
        </div>
        <div className="card">
          <Empty title="Only an admin can manage backups" />
        </div>
      </div>
    );
  }

  const list = (res.data && res.data.backups) || [];
  const sorted = list.slice().sort((a, b) => (toDate(b.created) || 0) - (toDate(a.created) || 0));
  const shown = showAll ? sorted : sorted.slice(0, SHOWN);

  return (
    <div className="app-page">
      <div className="app-head">
        <h1 className="page-title">Backups</h1>
        <button type="button" className="btn btn-primary head-btn" style={{ marginLeft: 'auto' }} disabled={!!busy} onClick={backupNow}>
          {busy === 'now' ? <span className="spinner" style={{ width: 14, height: 14 }} /> : <Icon name="plus" size={15} />} Back up now
        </button>
      </div>
      {!res.data ? (
        res.error ? <ErrorBox error={res.error} onRetry={res.reload} /> : <Loading />
      ) : (
        <div className="bk-grid">
          <div className="bk-col">
            <section className="card prof-card" aria-labelledby="bk-auto">
              <h2 id="bk-auto" className="prof-title">
                Nightly backup
              </h2>
              {settings ? (
                <div className="bk-auto">
                  <Switch checked={settings.auto} onChange={(v) => saveSettings({ auto: v })} label={settings.auto ? 'On' : 'Off'} />
                  <label className="bk-inline">
                    at
                    <input type="time" className="input input--sm" value={settings.time} disabled={!settings.auto} onChange={(e) => e.target.value && saveSettings({ time: e.target.value })} aria-label="Backup time" />
                  </label>
                  <label className="bk-inline">
                    keep the last
                    <input
                      type="number"
                      min={1}
                      max={90}
                      className="input input--sm bk-num"
                      value={settings.keep}
                      disabled={!settings.auto}
                      aria-label="How many to keep"
                      onChange={(e) => setSettings((s) => ({ ...s, keep: e.target.value }))}
                      onBlur={(e) => {
                        const n = Math.max(1, Math.min(90, Math.round(Number(e.target.value) || 7)));
                        saveSettings({ keep: n });
                      }}
                    />
                    {Number(settings.keep) === 1 ? 'backup' : 'backups'}
                  </label>
                </div>
              ) : null}
            </section>

            <section className="card prof-card" aria-labelledby="bk-dl">
              <h2 id="bk-dl" className="prof-title">
                Download a backup
              </h2>
              <label className="field">
                <span className="field-label">Lock with a passphrase (optional) - the file holds your connections' keys</span>
                <input className="input" type="password" autoComplete="new-password" value={pass} onChange={(e) => setPass(e.target.value)} placeholder="At least 8 characters" aria-invalid={passBad || undefined} />
                {passBad ? <span className="help amber">At least 8 characters, or leave it empty.</span> : <span className="help">Also used when you download a saved backup below. You'll need it to restore that file.</span>}
              </label>
              <div className="inline-form">
                <button type="button" className="btn" disabled={!!busy || passBad} onClick={() => download(null)}>
                  {busy === 'dl:new' ? <span className="spinner" style={{ width: 14, height: 14 }} /> : <Icon name="download" size={15} />} Download a backup
                </button>
              </div>
            </section>

            <section className="card prof-card" aria-labelledby="bk-file">
              <h2 id="bk-file" className="prof-title">
                Restore from a file
              </h2>
              <div className="bk-file">
                <input ref={fileInput} type="file" className="sr-only" id="bk-file-input" onChange={(e) => (setFile(e.target.files[0] || null), setFileError(''))} />
                <label htmlFor="bk-file-input" className="btn">
                  <Icon name="open" size={15} /> {file ? 'Choose another file' : 'Choose a backup file'}
                </label>
                <span className="bk-file-name">{file ? file.name : 'No file chosen'}</span>
              </div>
              <label className="field">
                <span className="field-label">Its passphrase, if it's locked</span>
                <input ref={filePassInput} className="input" type="password" autoComplete="off" value={filePass} onChange={(e) => (setFilePass(e.target.value), setFileError(''))} />
              </label>
              {fileError ? <div className="help amber" role="alert">{fileError}</div> : null}
              <div className="inline-form">
                <button type="button" className="btn" disabled={!file || !!busy} onClick={() => restore({ file, passphrase: filePass })}>
                  {busy === 'file' ? <span className="spinner" style={{ width: 14, height: 14 }} /> : <Icon name="refresh" size={15} />} Restore…
                </button>
              </div>
            </section>
          </div>

          <section className="card prof-card bk-saved" aria-labelledby="bk-list">
            <h2 id="bk-list" className="prof-title">
              Saved backups <span className="muted">{list.length ? `· ${list.length}` : ''}</span>
            </h2>
            {!list.length ? (
              <div className="muted">None yet. Use “Back up now”, or turn on the nightly backup.</div>
            ) : (
              <div className={'bk-rows' + (showAll ? ' is-all' : '')}>
                {shown.map((b, i) => (
                  <div key={b.name} className="bk-row">
                    <span className="bk-when">
                      <span className="strong">{fmtDate(b.created) || b.name}</span>
                      <span className="muted small">
                        {fmtSize(b.size)}
                        {i === 0 ? ' · newest' : ''}
                      </span>
                    </span>
                    <button type="button" className="btn btn-sm2" disabled={!!busy || passBad} onClick={() => download(b.name)} aria-label={`Download the backup from ${fmtDate(b.created)}`}>
                      {busy === 'dl:' + b.name ? <span className="spinner" style={{ width: 13, height: 13 }} /> : <Icon name="download" size={14} />} Download
                    </button>
                    <button type="button" className="btn btn-sm2" disabled={!!busy} onClick={() => restore({ name: b.name })} aria-label={`Restore the backup from ${fmtDate(b.created)}`}>
                      {busy === 'rs:' + b.name ? <span className="spinner" style={{ width: 13, height: 13 }} /> : <Icon name="refresh" size={14} />} Restore
                    </button>
                    <button type="button" className="icon-btn" disabled={!!busy} onClick={() => remove(b)} aria-label={`Delete the backup from ${fmtDate(b.created)}`} title="Delete">
                      <Icon name="trash" size={15} />
                    </button>
                  </div>
                ))}
              </div>
            )}
            {sorted.length > SHOWN ? (
              <button type="button" className="btn-link" onClick={() => setShowAll((v) => !v)}>
                {showAll ? 'Show fewer' : `Show all ${sorted.length}`}
              </button>
            ) : null}
          </section>
        </div>
      )}
      {restarting ? <Restarting /> : null}
    </div>
  );
}
