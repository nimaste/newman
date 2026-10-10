import { useHub } from '../hooks.js';
import { fmtClock, fmtWhen, joinList } from '../format.js';
import { Empty, ErrorBox, Loading } from './Controls.jsx';

export function statusPill(status) {
  const cls = { sent: 'pill--sent', resolved: 'pill--resolved', failed: 'pill--failed' }[status] || '';
  return <span className={'pill ' + cls}>{status || 'unknown'}</span>;
}

/** Recent runs of one automation, newest first, with expandable logs. */
export default function RunsList({ res }) {
  const { entitiesById } = useHub();
  if (res.data === undefined) {
    return res.error ? <ErrorBox error={res.error} onRetry={res.reload} /> : <Loading>Loading runs…</Loading>;
  }
  const runs = (res.data || []).slice().sort((a, b) => (b.startedAt || 0) - (a.startedAt || 0)).slice(0, 20);
  if (!runs.length) return <Empty title="No runs yet">Use “Test run” to send it now.</Empty>;

  return (
    <div>
      {runs.map((r) => {
        const pending = (r.pending || []).map((id) => (entitiesById[id] ? entitiesById[id].name : id));
        const detail = [
          r.resolvedBy ? `by ${r.resolvedBy}` : null,
          pending.length && r.status === 'sent' ? `waiting on ${joinList(pending)}` : null,
        ]
          .filter(Boolean)
          .join(' · ');
        return (
          <details key={r.id} className="run">
            <summary>
              {statusPill(r.status)}
              <span className="run-title">
                {fmtWhen(r.startedAt)}
                {detail ? <span className="muted" style={{ fontWeight: 500 }}> · {detail}</span> : null}
              </span>
              <span className="muted" style={{ fontSize: 13 }}>
                {(r.log || []).length} log line{(r.log || []).length === 1 ? '' : 's'}
              </span>
            </summary>
            {(r.log || []).length ? (
              <ol className="run-log">
                {r.log.map((l, i) => (
                  <li key={i}>
                    <time>{fmtClock(l.ts)}</time>
                    {l.text}
                  </li>
                ))}
              </ol>
            ) : null}
          </details>
        );
      })}
    </div>
  );
}
