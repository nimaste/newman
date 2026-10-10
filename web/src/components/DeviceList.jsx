import { useHub } from '../hooks.js';
import { Empty } from './Controls.jsx';
import { DeviceControls, EntityName, useDeviceActions } from './DeviceControls.jsx';
import { entityStateText, groupByArea, isTransitional, issuesOf, stateClass } from '../model.js';
import { plural } from '../format.js';

/** Live state text; opening/closing gets a small animated indicator. */
export function StateText({ entity, kind, className = '', long = false }) {
  const moving = isTransitional(entity, kind);
  const full = entityStateText(entity, kind, { long: true });
  const text = long ? full : entityStateText(entity, kind);
  return (
    <span className={(moving ? 'state-moving ' : stateClass(entity, kind) + ' ') + className} title={text !== full ? full : undefined}>
      {moving ? <span className="moving-dot" aria-hidden="true" /> : null}
      {text}
    </span>
  );
}

/**
 * Devices grouped by kind (plural name from meta), then by room when known,
 * each row with live state and the same controls as the Devices page.
 */
export default function DeviceList({ entities, emptyText }) {
  const { kinds, kindsById } = useHub();
  const { run, rename, busy } = useDeviceActions();

  if (!entities.length) return <Empty title="No devices yet">{emptyText || 'Devices appear here as soon as the integration finds them.'}</Empty>;

  const byKind = kinds.map((k) => ({ kind: k, items: entities.filter((e) => e.kind === k.id) })).filter((g) => g.items.length);
  const orphans = entities.filter((e) => !kindsById[e.kind]);
  if (orphans.length) byKind.push({ kind: { id: '_other', plural: 'Other' }, items: orphans });

  return (
    <div className="dev-list">
      {byKind.map((g) => (
        <section key={g.kind.id} className="dev-kind" aria-label={g.kind.plural || g.kind.name}>
          <h3 className="eyebrow eyebrow--sm">
            {g.kind.plural || g.kind.name} <span className="dev-count">{g.items.length}</span>
          </h3>
          {groupByArea(g.items).map((a) => (
            <div key={a.area || '_'} className="dev-area">
              {a.area !== null ? <div className="dev-area-name">{a.area}</div> : null}
              {a.items.map((e) => {
                const kind = kindsById[e.kind];
                const issues = issuesOf(e);
                return (
                  <div key={e.id} className="dev-row">
                    <div className="dev-main">
                      <EntityName entity={e} onRename={(n) => rename(e, n)} />
                      <div className="dev-sub">
                        <StateText entity={e} kind={kind} />
                        {issues.length ? <span className="amber"> · {issues.join(' · ')}</span> : null}
                        {(e.usedBy || []).length ? <span className="muted"> · {plural(e.usedBy.length, 'automation')}</span> : null}
                      </div>
                    </div>
                    <div className="cell-actions">
                      <DeviceControls entity={e} kind={kind} busy={busy[e.id]} run={run} />
                    </div>
                  </div>
                );
              })}
            </div>
          ))}
        </section>
      ))}
    </div>
  );
}
