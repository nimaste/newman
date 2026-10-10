// A wall tablet's voice settings (Household → Accounts → the tablet): voice on/off, its wake word
// (every model in the hub's voice/ folder) and how keenly it listens for it, the reply volume and voice, and whether unlocking / opening / disarming
// may be done by voice (off: the tablet says "For safety, do that from the screen."), and teaching /
// training "Hey Newman" with the household's voices (VoiceTraining.jsx).

import { useEffect, useState } from 'react';
import { api, enc, toast } from '../api.js';
import { useApi } from '../hooks.js';
import { Check, Segmented } from '../components/Controls.jsx';
import VoiceTraining from './VoiceTraining.jsx';

const SENSITIVITY = [
  { id: 'low', label: 'Low' },
  { id: 'normal', label: 'Normal' },
  { id: 'high', label: 'High' },
];

export default function VoiceSettings({ user }) {
  const res = useApi('api/voice/settings/' + enc(user.id));
  const voices = useApi('api/voice/voices');
  const words = useApi('api/voice/wakewords');
  const [s, setS] = useState(null);
  const [volume, setVolume] = useState(null);
  useEffect(() => {
    if (res.data) {
      setS(res.data);
      setVolume(res.data.volume);
    }
  }, [res.data]);
  if (!s) return <span className="muted small">{res.error ? "Couldn't load the voice settings" : 'Loading…'}</span>;

  const save = async (changes, done) => {
    const before = s;
    setS({ ...s, ...changes });
    try {
      const saved = await api.patch('api/voice/settings/' + enc(user.id), changes);
      setS(saved);
      if (done) toast(done, 'success');
    } catch {
      setS(before);
    }
  };
  const list = (voices.data && voices.data.voices) || [];
  const fallback = list.find((v) => v.name === (voices.data && voices.data.default));
  const wakeWords = (words.data && words.data.wakeWords) || [];
  const word = wakeWords.find((w) => w.name === s.wakeWord);
  const listensFor = word ? `Listens for “${word.phrase}”` : 'Listens for its wake word';
  return (
    <div className="voice-settings">
      <Check checked={s.enabled} onChange={(on) => save({ enabled: on }, on ? `${user.name} ${listensFor.charAt(0).toLowerCase()}${listensFor.slice(1)}` : `${user.name} doesn't listen any more`)}>
        {listensFor}
      </Check>
      {s.enabled ? (
        <>
          {wakeWords.length ? (
            <label className="voice-row">
              <span className="voice-label">Wake word</span>
              <select
                className="select select--sm hh-fact-select"
                value={s.wakeWord}
                onChange={(e) => {
                  const w = wakeWords.find((x) => x.name === e.target.value);
                  save({ wakeWord: e.target.value }, w ? `${user.name} now listens for “${w.phrase}”` : null);
                }}
                aria-label="Wake word"
              >
                {wakeWords.map((w) => (
                  <option key={w.name} value={w.name}>
                    {w.phrase}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          <div className="voice-row">
            <span className="voice-label">Wake sensitivity</span>
            <Segmented value={s.sensitivity} options={SENSITIVITY} onChange={(v) => save({ sensitivity: v })} label="Wake sensitivity" />
          </div>
          <label className="voice-row">
            <span className="voice-label">Reply volume</span>
            <span className="slider voice-slider">
              <input
                type="range"
                min={0}
                max={100}
                step={5}
                value={volume ?? s.volume}
                aria-label="Reply volume"
                onChange={(e) => setVolume(Number(e.target.value))}
                onPointerUp={() => volume !== s.volume && save({ volume })}
                onKeyUp={() => volume !== s.volume && save({ volume })}
              />
              <span className="muted small">{volume ?? s.volume}%</span>
            </span>
          </label>
          <label className="voice-row">
            <span className="voice-label">Voice</span>
            <select className="select select--sm hh-fact-select" value={s.voice} onChange={(e) => save({ voice: e.target.value })} aria-label="Reply voice">
              <option value="">{fallback ? `${fallback.label} (the hub's default)` : "The hub's default"}</option>
              {list
                .filter((v) => !fallback || v.name !== fallback.name)
                .map((v) => (
                  <option key={v.name} value={v.name}>
                    {v.label}
                  </option>
                ))}
            </select>
          </label>
          <Check
            checked={s.allowSensitive}
            onChange={(on) => save({ allowSensitive: on }, on ? 'Unlocking and opening by voice is allowed on this tablet' : 'Unlocking and opening need the screen again')}
          >
            Allow unlocking, opening and disarming by voice
          </Check>
          <div className="muted small hh-fact-note">
            {s.allowSensitive
              ? 'Anyone near the tablet can unlock doors or open the garage by voice.'
              : 'Asked by voice, it answers “For safety, do that from the screen.”'}
          </div>
        </>
      ) : null}
      <VoiceTraining tablet={user} />
    </div>
  );
}
