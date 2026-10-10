// Teaching and training "Hey Newman" with the household's own voices (in a wall tablet's Voice
// settings, Household → Accounts → the tablet). Teach: pick who's recording and start it on this
// tablet - the tablet records "Hey Newman" ~30 times and half a minute of ordinary talking (the hub
// keeps the clips: api/voice/teach). Train: the newman-wakeword-trainer container trains a new
// version from them (api/voice/train); its numbers are shown next to the one in use, and "Use it"
// installs it on every tablet (Undo puts the old one back). README → Voice → Teaching and training.

import { useEffect, useState } from 'react';
import { api, enc, toast } from '../api.js';
import { useApi } from '../hooks.js';

const pct = (x) => (x === null || x === undefined ? '—' : `${Math.round(x * 100)}%`);
const perHour = (x) => (x === null || x === undefined ? '—' : x < 0.05 ? 'almost none' : `${Number(x).toFixed(1)}`);
const minutes = (s) => (s < 90 ? 'about a minute' : `about ${Math.round(s / 60)} minutes`);
const when = (t) => (t ? new Date(t * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : '');

function Teach({ tablet, list }) {
  const people = useApi('api/people');
  const [who, setWho] = useState('');
  const [other, setOther] = useState('');
  const [busy, setBusy] = useState(false);
  const names = ((Array.isArray(people.data) && people.data) || []).map((p) => p.name).filter(Boolean);
  const data = list.data;
  const active = data && (data.teaching || []).find((t) => t.tablet === tablet.id);
  const person = who === '__other' ? other.trim() : who || names[0] || '';

  const start = async () => {
    if (!person) return;
    setBusy(true);
    try {
      await api.post('api/voice/teach/start', { tablet: tablet.id, person });
      toast(`${tablet.name} is ready - ${person} can start recording there`, 'success');
      list.reload();
    } catch {
      /* toast shown */
    }
    setBusy(false);
  };
  const stop = async () => {
    await api.post('api/voice/teach/stop', { tablet: tablet.id }).catch(() => {});
    list.reload();
  };
  const forget = async (p) => {
    if (!window.confirm(`Delete all of ${p.name}'s recordings?`)) return;
    await api.del('api/voice/teach/' + enc(p.slug)).catch(() => {});
    list.reload();
  };

  return (
    <div className="voice-block">
      <div className="voice-subhead">Teach “Hey Newman”</div>
      <div className="muted small">
        Each person says “Hey Newman” about 30 times at this tablet, then talks normally for half a minute, so it learns your voices. The recordings stay on the hub.
      </div>
      {active ? (
        <div className="voice-teach-live">
          <span>
            Recording on {tablet.name}: <b>{active.person}</b>
          </span>
          <button type="button" className="btn btn-sm2" onClick={stop}>
            Stop
          </button>
        </div>
      ) : (
        <div className="voice-teach-start">
          <select className="select select--sm hh-fact-select" value={who || (names[0] ? names[0] : '__other')} onChange={(e) => setWho(e.target.value)} aria-label="Who's recording">
            {names.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
            <option value="__other">Someone else…</option>
          </select>
          {who === '__other' || !names.length ? (
            <input className="input input--sm" placeholder="Their name" value={other} onChange={(e) => { setWho('__other'); setOther(e.target.value); }} aria-label="Their name" maxLength={60} />
          ) : null}
          <button type="button" className="btn btn-primary btn-sm2" disabled={busy || !person} onClick={start}>
            Start on this tablet
          </button>
        </div>
      )}
      {data && data.people.length ? (
        <ul className="voice-recordings">
          {data.people.map((p) => (
            <li key={p.slug}>
              <span>
                <b>{p.name}</b> <span className="muted">· {p.positive} “Hey Newman” · {p.negative ? `${p.negative} talking` : 'no talking yet'}</span>
              </span>
              <button type="button" className="btn-link" onClick={() => forget(p)}>
                Delete
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function Compare({ cand, current }) {
  const m = cand.metrics || {};
  const now = m.current || {};
  const rows = [
    ['Catches your voices', pct(now.realRecall), pct(m.realRecall)],
    ['False wake-ups per hour', perHour(now.falseWakesPerHour ?? (current && current.falseWakesPerHour)), perHour(m.falseWakesPerHour)],
  ];
  return (
    <table className="voice-compare">
      <thead>
        <tr>
          <th />
          <th>In use now</th>
          <th>New version</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(([label, a, b]) => (
          <tr key={label}>
            <th scope="row">{label}</th>
            <td>{a}</td>
            <td>
              <b>{b}</b>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Train({ list }) {
  const [poll, setPoll] = useState(false);
  const res = useApi('api/voice/train', { interval: poll ? 4000 : 0 });
  const d = res.data;
  const running = !!(d && (d.status.state === 'running' || d.pending));
  useEffect(() => setPoll(running), [running]);
  if (!d) return <span className="muted small">{res.error ? "Couldn't load training" : 'Loading…'}</span>;
  const s = d.status;
  const cand = d.candidates[0];
  const enough = d.recordings.positive >= d.minClips;

  const act = async (fn, done) => {
    try {
      res.setData(await fn());
      if (done) toast(done, 'success');
    } catch {
      /* toast shown */
    }
    list.reload();
  };
  const train = () => act(() => api.post('api/voice/train', {}), 'Training started - it takes about half an hour');
  const use = () => act(() => api.post('api/voice/train/use/' + enc(cand.name)), 'The new “Hey Newman” is on every tablet now');
  const keep = () => act(() => api.del('api/voice/train/candidates/' + enc(cand.name)), 'Kept the one in use');
  const undo = () => act(() => api.post('api/voice/train/undo'), 'Back to the earlier version');

  return (
    <div className="voice-block">
      <div className="voice-subhead">Train “Hey Newman”</div>
      <div className="muted small">
        {d.recordings.positive} recordings from {d.recordings.people} {d.recordings.people === 1 ? 'person' : 'people'}
        {enough ? '.' : ` - record at least ${d.minClips} before training.`}
        {d.current ? ` In use: ${d.current.installedAt ? `trained ${when(d.current.installedAt)}` : 'the first version (computer voices only)'}.` : ''}
      </div>
      {!s.trainerOnline && !running ? (
        <div className="muted small hh-fact-note">The trainer isn't running on the server (README → Voice → Teaching and training “Hey Newman”).</div>
      ) : null}
      {running ? (
        <div className="voice-progress" role="status">
          <div className="voice-progress-bar">
            <span style={{ width: `${Math.max(2, Math.min(100, s.state === 'running' ? s.percent : 0))}%` }} />
          </div>
          <span className="muted small">
            {s.state === 'running' ? `${s.stage || 'Training'}… ${Math.round(s.percent)}%${s.eta ? ` · ${minutes(s.eta)} left` : ''}` : 'Waiting for the trainer to start…'}
          </span>
        </div>
      ) : null}
      {s.state === 'failed' && !running ? <div className="small voice-error">Training didn't finish: {s.message}</div> : null}
      {cand ? (
        <div className="voice-candidate">
          <div className="small">
            A new version is ready ({when(cand.createdAt)}). Tested on {cand.metrics.realHeldOut || 0} of your recordings it didn't learn from, and on 11 hours of everyday sound:
          </div>
          <Compare cand={cand} current={d.current} />
          {cand.metrics.byPerson && cand.metrics.byPerson.length > 1 ? (
            <div className="muted small">
              {cand.metrics.byPerson.map((p) => `${p.name} ${pct(p.recall)}`).join(' · ')}
            </div>
          ) : null}
          <div className="voice-actions">
            <button type="button" className="btn btn-primary btn-sm2" onClick={use}>
              Use it
            </button>
            <button type="button" className="btn btn-sm2" onClick={keep}>
              Keep current
            </button>
          </div>
        </div>
      ) : null}
      <div className="voice-actions">
        {!running ? (
          <button type="button" className="btn btn-sm2" disabled={!enough} onClick={train}>
            {cand ? 'Train again' : 'Train'}
          </button>
        ) : null}
        {d.previous ? (
          <button type="button" className="btn-link" onClick={undo}>
            Undo - go back to the earlier version
          </button>
        ) : null}
      </div>
    </div>
  );
}

export default function VoiceTraining({ tablet }) {
  const [poll, setPoll] = useState(false);
  const list = useApi('api/voice/teach', { interval: poll ? 5000 : 0 });
  const teaching = !!(list.data && (list.data.teaching || []).length);
  useEffect(() => setPoll(teaching), [teaching]);
  return (
    <div className="voice-training">
      <Teach tablet={tablet} list={list} />
      <Train list={list} />
    </div>
  );
}
