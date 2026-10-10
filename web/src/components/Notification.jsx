import { Icon } from '../icons.jsx';

/** A phone-style notification bubble. */
export function NotificationBubble({ tag, title, body, image }) {
  return (
    <div className="notif">
      {tag ? <div className="notif-tag">{tag}</div> : null}
      {title ? <div className="notif-title">{title}</div> : null}
      {body ? <div className="notif-body">{body}</div> : null}
      {image ? <img className="notif-img" src={image} alt="Snapshot" loading="lazy" /> : null}
    </div>
  );
}

function buttonClass(b) {
  if (b.sensitive) return ' is-sensitive';
  if (b.icon === 'lock') return ' is-lock';
  return '';
}

/**
 * The action buttons under a notification. With onPress they are real
 * buttons (the developer outbox on Phones); without, a static preview.
 */
export function NotificationButtons({ buttons, onPress, busyId }) {
  if (!buttons || !buttons.length) return null;
  return (
    <div className="notif-buttons">
      {buttons.map((b) =>
        onPress ? (
          <button key={b.id} type="button" className={'notif-button' + buttonClass(b)} onClick={() => onPress(b)} disabled={!!busyId}>
            <span>{b.label}</span>
            {busyId === b.id ? <span className="spinner" style={{ width: 16, height: 16 }} /> : <Icon name={b.icon || 'dot'} size={16} />}
          </button>
        ) : (
          <div key={b.id} className={'notif-button' + buttonClass(b)}>
            <span>{b.label}</span>
            <Icon name={b.icon || 'dot'} size={16} />
          </div>
        ),
      )}
    </div>
  );
}

/** Live preview panel of the automation editor, fed by /describe. */
export function PhonePreview({ preview, notifyEnabled, name }) {
  return (
    <section className="preview" aria-labelledby="preview-label" aria-live="polite">
      <div id="preview-label" className="preview-label">
        Preview on the phone
      </div>
      {!preview ? (
        <div className="preview-empty">The preview appears as soon as the hub has described this automation.</div>
      ) : !notifyEnabled ? (
        <div className="preview-empty">No notification is sent — this automation acts silently.</div>
      ) : (
        <>
          <NotificationBubble
            tag={'Newman' + (preview.timeSensitive ? ' · Time sensitive' : '')}
            title={preview.title || name}
            body={preview.body}
          />
          <NotificationButtons buttons={preview.buttons} />
        </>
      )}
      {preview && preview.followUp ? <NotificationBubble tag="Newman · Follow-up" title={name} body={preview.followUp} /> : null}
    </section>
  );
}
