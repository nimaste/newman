import { useActivityFeed, useHub } from '../hooks.js';
import { Empty, ErrorBox, Loading } from '../components/Controls.jsx';
import { fmtNext, fmtWhen, greeting, joinList, plural } from '../format.js';
import { activeLabel, isActive, isModeKind, issuesOf, notSecure } from '../model.js';

/** What the Security tile watches, from /api/meta alone: every kind that has a secure state. */
function securityKind(kind) {
  return !!(kind && kind.secureState);
}

function Tile({ label, value, detail, tone, href }) {
  const body = (
    <>
      <div className="tile-label">{label}</div>
      <div className="tile-value">{value}</div>
      {detail ? <div className="tile-detail">{detail}</div> : null}
    </>
  );
  const cls = 'tile' + (tone ? ' tile--' + tone : '');
  return href ? (
    <a className={cls} href={href} style={{ color: 'inherit', textDecoration: 'none', fontWeight: 'inherit' }}>
      {body}
    </a>
  ) : (
    <div className={cls}>{body}</div>
  );
}

function securityTile(entities, kindsById) {
  const relevant = entities.filter((e) => securityKind(kindsById[e.kind]));
  const kinds = [...new Set(relevant.map((e) => e.kind))].map((id) => kindsById[id]);
  if (!relevant.length) {
    return { value: 'No locks yet', detail: 'Connect locks or door sensors under Connections.', tone: null };
  }
  const active = relevant.filter((e) => notSecure(e, kindsById[e.kind]));
  const offline = relevant.filter((e) => e.available === false);
  if (active.length) {
    const labels = [...new Set(active.map((e) => (e.stateLabel || e.state || '').toLowerCase()))];
    return {
      value: labels.length === 1 ? `${active.length} ${labels[0]}` : `${active.length} not secure`,
      detail: active.map((e) => `${e.name} ${(e.stateLabel || e.state || '').toLowerCase()}`).join(' · '),
      tone: 'alert',
    };
  }
  const parts = kinds
    .map((k) => {
      const mine = relevant.filter((e) => e.kind === k.id && e.available !== false);
      if (!mine.length) return null;
      const counts = {};
      mine.forEach((e) => {
        const l = (e.stateLabel || e.state || '').toLowerCase();
        counts[l] = (counts[l] || 0) + 1;
      });
      const top = Object.entries(counts).sort((a, b) => b[1] - a[1])[0][0];
      return `${mine.length} ${(mine.length === 1 ? k.name : k.plural || k.name).toLowerCase()} ${top}`;
    })
    .filter(Boolean);
  if (offline.length) parts.push(`${offline.length} offline`);
  return { value: offline.length ? 'Secure, mostly' : 'All secure', detail: parts.join(' · '), tone: offline.length ? 'warn' : 'teal' };
}

export default function Overview() {
  const { entities, kindsById, categories, status } = useHub();
  const events = useActivityFeed(30);

  if (!entities) {
    return (
      <div className="app-page">
        <div className="app-head">
          <h1 className="page-title">{greeting()}</h1>
        </div>
        <Loading>Loading devices…</Loading>
      </div>
    );
  }

  const sec = securityTile(entities, kindsById);

  // The house-mode tile: the first device whose kind isMode (an alarm).
  const alarm = entities.find((e) => isModeKind(kindsById[e.kind]));
  const alarmKind = alarm && kindsById[alarm.kind];
  // A mode no command sets (entry delay, triggered) is something happening.
  const alarmAlert = !!alarmKind && !!alarm.state && !(alarmKind.commands || []).some((c) => c.confirmsTo === alarm.state) && !(alarmKind.transitional || []).includes(alarm.state);

  const nextRuns = ((status && status.nextRuns) || []).slice().sort((a, b) => (a.at || 0) - (b.at || 0));
  const next = nextRuns[0];

  const attention = entities.filter((e) => issuesOf(e).length);

  const cats = categories.map((c) => {
    const mine = entities.filter((e) => e.category === c.id);
    if (!mine.length) return { ...c, detail: 'not connected', cls: 'muted' };
    const problems = mine.filter((e) => issuesOf(e).length);
    const unsecured = mine.filter((e) => notSecure(e, kindsById[e.kind]));
    // Other devices that are on/active (lights on…): informational. A mode device's state isn't "active" here.
    const active = mine.filter((e) => !kindsById[e.kind]?.secureState && isActive(e, kindsById[e.kind]) && !isModeKind(kindsById[e.kind]));
    const secureable = mine.some((e) => securityKind(kindsById[e.kind]));
    let detail = plural(mine.length, 'device');
    let cls = 'good';
    if (problems.length) {
      detail += ` · ${problems.length} need${problems.length === 1 ? 's' : ''} attention`;
      cls = 'amber';
    } else if (unsecured.length) {
      const labels = [...new Set(unsecured.map((e) => (e.stateLabel || e.state || '').toLowerCase()))];
      detail += ` · ${unsecured.length} ${labels.length === 1 ? labels[0] : 'not secure'}`;
      cls = 'amber';
    } else if (active.length) {
      const labels = [...new Set(active.map((e) => activeLabel(kindsById[e.kind]).toLowerCase()))];
      detail += ` · ${active.length} ${labels.length === 1 ? labels[0] : 'active'}`;
    } else if (secureable) {
      detail += ' · all secure';
    }
    return { ...c, detail, cls };
  });

  return (
    <div className="app-page">
      <div className="app-head">
        <h1 className="page-title">{greeting()}</h1>
      </div>

      <div className="tiles tiles--fixed">
        <Tile label="Security" value={sec.value} detail={sec.detail} tone={sec.tone} href="#/devices" />
        <Tile
          label={alarmKind ? alarmKind.name : 'Alarm'}
          value={alarm ? (alarm.available === false ? 'Offline' : alarm.stateLabel || alarm.state) : 'No alarm'}
          detail={alarm ? [alarm.name, alarm.integrationName].filter(Boolean).join(' · ') : 'Not connected'}
          tone={alarmAlert ? 'alert' : null}
        />
        <Tile
          label="Next up"
          value={next ? fmtNext(next.at) : 'Nothing scheduled'}
          detail={next ? next.name : 'Time-of-day automations show here.'}
          href={next ? '#/automations/' + encodeURIComponent(next.automationId) : '#/automations'}
        />
        <Tile
          label="Attention"
          value={attention.length ? plural(attention.length, 'device') : 'All good'}
          detail={
            attention.length
              ? attention.map((e, i) => (
                  <span key={e.id}>
                    {i ? ' · ' : ''}
                    <a href={'#/devices/' + encodeURIComponent(e.id)} className="quiet-link">
                      {e.name}
                    </a>
                    : {joinList(issuesOf(e), 'and').toLowerCase()}
                  </span>
                ))
              : 'No batteries, tamper or offline issues.'
          }
          tone={attention.length ? 'warn' : null}
        />
      </div>

      <div className="ov-grid">
        <section className="pane" aria-labelledby="cats-title">
          <h2 id="cats-title" className="pane-title">
            Categories
          </h2>
          <div className="pane-body pane-body--list">
            {cats.map((c) => (
              <a key={c.id} className="row row--link" href={c.detail === 'not connected' ? '#/integrations' : '#/devices?c=' + encodeURIComponent(c.id)}>
                <span className="strong">{c.name}</span>
                <span className={c.cls} style={{ fontSize: 14, fontWeight: 600 }}>
                  {c.detail === 'not connected' ? 'not connected — add a connection' : c.detail}
                </span>
              </a>
            ))}
          </div>
        </section>

        <section className="pane" aria-labelledby="recent-title">
          <h2 id="recent-title" className="pane-title">
            Recent activity <a href="#/activity" style={{ fontSize: 13 }}>See all</a>
          </h2>
          <div className="pane-body pane-body--list">
          {events.data === undefined ? (
            events.error ? <ErrorBox error={events.error} onRetry={events.reload} /> : <Loading />
          ) : !events.data.length ? (
            <Empty title="Nothing yet">Lock changes, doorbell presses and automation runs show up here.</Empty>
          ) : (
            <div className="rows">
              {events.data.map((e) => (
                <div key={e.id} className="row">
                  <span>
                    <span className="strong">{e.title}</span>
                    {e.body ? <span className="muted"> · {e.body}</span> : null}
                  </span>
                  <span className="row-time">{fmtWhen(e.ts)}</span>
                </div>
              ))}
            </div>
          )}
          </div>
        </section>
      </div>
    </div>
  );
}
