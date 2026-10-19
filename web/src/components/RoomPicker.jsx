// Choosing a room: one of the rooms already in use (GET api/rooms), a new one, or none.

import { useState } from 'react';
import { useApi } from '../hooks.js';

const NEW = '\u0000new';

/** A select of rooms plus "New room…" (a text box) and "No room". `value` is a room name or null. */
export function RoomSelect({ value, onChange, id, disabled, noneLabel = 'No room' }) {
  const rooms = useApi('api/rooms');
  const list = [...new Set([...(rooms.data || []), ...(value ? [value] : [])])].sort((a, b) => a.localeCompare(b));
  const [adding, setAdding] = useState(false);
  const [text, setText] = useState('');
  if (adding) {
    return (
      <span className="room-new">
        <input
          id={id}
          className="input input--sm"
          autoFocus
          maxLength={40}
          placeholder="Room name"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && text.trim()) {
              e.preventDefault();
              onChange(text.trim());
              setAdding(false);
            }
            if (e.key === 'Escape') setAdding(false);
          }}
        />
        <button type="button" className="btn btn-sm" disabled={!text.trim()} onClick={() => (onChange(text.trim()), setAdding(false))}>
          Add
        </button>
        <button type="button" className="btn-link" onClick={() => setAdding(false)}>
          Cancel
        </button>
      </span>
    );
  }
  return (
    <select
      id={id}
      className="input input--sm"
      value={value || ''}
      disabled={disabled}
      onChange={(e) => {
        if (e.target.value === NEW) {
          setText('');
          setAdding(true);
        } else onChange(e.target.value || null);
      }}
    >
      <option value="">{noneLabel}</option>
      {list.map((r) => (
        <option key={r} value={r}>
          {r}
        </option>
      ))}
      <option value={NEW}>New room…</option>
    </select>
  );
}
