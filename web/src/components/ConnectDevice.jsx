// "Connect a device": the connect link (newman://connect?...) as a QR code for the Newman phone and
// wall apps, which scan it on their "Connect to your Newman" screen. The link carries only this hub's
// id, name and addresses - nothing secret.

import { useMemo, useState } from 'react';
import qrcode from 'qrcode-generator';
import { toast } from '../api.js';
import { useApi } from '../hooks.js';
import { ErrorBox, Loading, Modal } from './Controls.jsx';

/** The link as an SVG QR code (one path; dark modules on a white quiet zone so any camera reads it). */
export function QrCode({ text, size = 232, label }) {
  const { d, n } = useMemo(() => {
    const qr = qrcode(0, 'M');
    qr.addData(text, 'Byte');
    qr.make();
    const count = qr.getModuleCount();
    let path = '';
    for (let r = 0; r < count; r += 1) {
      for (let c = 0; c < count; c += 1) if (qr.isDark(r, c)) path += `M${c + 4} ${r + 4}h1v1h-1z`;
    }
    return { d: path, n: count + 8 };
  }, [text]);
  return (
    <svg className="qr" width={size} height={size} viewBox={`0 0 ${n} ${n}`} role="img" aria-label={label} shapeRendering="crispEdges">
      <rect width={n} height={n} fill="#ffffff" />
      <path d={d} fill="#111111" />
    </svg>
  );
}

export default function ConnectDevice({ onClose, forName }) {
  const info = useApi('api/connect-info');
  const [copied, setCopied] = useState(false);
  const data = info.data;
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(data.link);
      setCopied(true);
    } catch {
      toast("Couldn't copy - select the link and copy it", 'error');
    }
  };
  return (
    <Modal title={forName ? `Connect ${forName}` : 'Connect a device'} onClose={onClose}>
      {data === undefined ? (
        info.error ? <ErrorBox error={info.error} onRetry={info.reload} /> : <Loading />
      ) : (
        <div className="connect-qr">
          <QrCode text={data.link} label={`QR code to connect to ${data.name}`} />
          <p className="help">Scan it with the Newman app on a phone or wall tablet. Nothing secret is in it: the device still signs in.</p>
          <dl className="hh-facts connect-facts">
            <dt>Hub</dt>
            <dd>{data.name}</dd>
            <dt>At home</dt>
            <dd>{data.localUrls && data.localUrls.length ? data.localUrls.join(', ') : 'No home-network address found'}</dd>
            <dt>Away</dt>
            <dd>{data.outsideUrl || 'No outside address'}</dd>
          </dl>
          <div className="connect-link">
            <code className="connect-link-text">{data.link}</code>
            <button type="button" className="btn btn-sm" onClick={copy}>
              {copied ? 'Copied' : 'Copy link'}
            </button>
          </div>
        </div>
      )}
    </Modal>
  );
}
