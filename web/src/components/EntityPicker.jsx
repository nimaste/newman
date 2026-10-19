import { useHub } from '../hooks.js';
import { Icon } from '../icons.jsx';
import { entityStateText, isRemote, stateClass } from '../model.js';
import { Empty } from './Controls.jsx';
import { roomOf } from '../model.js';

/**
 * Device picker grouped by kind, with live state. Multi-select renders
 * checkbox rows; `single` renders radio rows.
 *   include(entity, kind) -> bool  limits which devices are offered
 */
export default function EntityPicker({ selected, onChange, include, single, label, emptyText }) {
  const { entities, kinds, kindsById } = useHub();
  const list = (entities || []).filter((e) => include(e, kindsById[e.kind]));
  // Keep selected-but-missing ids visible so they can be removed.
  const missing = selected.filter((id) => !(entities || []).some((e) => e.id === id));

  if (!list.length && !missing.length) {
    return (
      <Empty title="No matching devices">
        {emptyText || 'Connect your devices first.'} <a href="#/integrations">Add a connection</a>
      </Empty>
    );
  }

  const toggle = (id) => {
    if (single) onChange(selected.includes(id) ? [] : [id]);
    else onChange(selected.includes(id) ? selected.filter((x) => x !== id) : [...selected, id]);
  };

  const groups = kinds.map((k) => ({ kind: k, items: list.filter((e) => e.kind === k.id) })).filter((g) => g.items.length);
  const orphans = list.filter((e) => !kindsById[e.kind]);
  if (orphans.length) groups.push({ kind: { id: '_other', plural: 'Other' }, items: orphans });

  const row = ({ id, name, right, cls }) => {
    const on = selected.includes(id);
    return (
      <button key={id} type="button" role={single ? 'radio' : 'checkbox'} aria-checked={on} className="pick-row" onClick={() => toggle(id)}>
        {single ? (
          <span className="radio" aria-hidden="true" />
        ) : (
          <span className="box" aria-hidden="true">
            {on ? <Icon name="check" size={14} strokeWidth={3} /> : null}
          </span>
        )}
        <span className="pick-name">{name}</span>
        <span className={'pick-meta ' + (cls || '')}>{right}</span>
      </button>
    );
  };

  return (
    <div className="picker" role={single ? 'radiogroup' : 'group'} aria-label={label}>
      {groups.map((g) => (
        <div key={g.kind.id} className="picker-group">
          <div className="eyebrow eyebrow--sm">{g.kind.plural || g.kind.name}</div>
          {g.items.map((e) =>
            row({
              id: e.id,
              name: e.name,
              cls: stateClass(e, kindsById[e.kind]),
              right: (
                <>
                  {e.state || e.available === false || isRemote(e, kindsById[e.kind]) ? (
                    <span>
                      {entityStateText(e, kindsById[e.kind])}
                      {e.available === false || isRemote(e, kindsById[e.kind]) ? '' : ' now'}
                    </span>
                  ) : null}
                  <span className="muted" style={{ fontWeight: 500 }}>
                    {e.state || e.available === false ? ' · ' : ''}
                    {[roomOf(e), e.integrationName || e.integration].filter(Boolean).join(' · ')}
                  </span>
                </>
              ),
            }),
          )}
        </div>
      ))}
      {missing.length ? (
        <div className="picker-group">
          <div className="eyebrow eyebrow--sm amber">No longer available</div>
          {missing.map((id) => row({ id, name: id, right: 'Missing', cls: 'amber' }))}
        </div>
      ) : null}
    </div>
  );
}
