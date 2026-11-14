import { useEffect, useMemo, useState } from "react";

/* ------------------------------------------------------------------ */
/* Copy                                                                */
/* ------------------------------------------------------------------ */

export const CHECKS = [
  { key: "presence", title: "Device is worn", detail: "Skin contact on the touch pad", Icon: TouchIcon },
  { key: "liveness", title: "Live heartbeat", detail: "Pulse read from the electrodes", Icon: HeartIcon },
  {
    key: "identity",
    title: "Fingerprint match",
    detail: "Checked on the sensor, never sent to this laptop",
    Icon: FingerprintIcon,
  },
];

export const REASONS = {
  not_present: {
    title: "Device isn’t being worn",
    body: "Put the band on so the touch pad sits against your skin, then try again.",
  },
  no_heartbeat: {
    title: "No heartbeat detected",
    body: "The electrodes couldn’t read a pulse. Check both pads are touching skin, then try again.",
  },
  wrong_finger: { title: "Fingerprint didn’t match", body: "Only an enrolled finger can approve this." },
  user_rejected: { title: "Rejected on the device", body: "The wearer declined on the device." },
  timeout: {
    title: "No response from the device",
    body: "Nothing came back in time. Check the device is connected and showing the request.",
  },
  cancelled: { title: "Cancelled", body: "You cancelled this request before it was approved." },
  device_error: { title: "The device reported an error", body: "Restart the device and try again." },
  bad_signature: {
    title: "Signature didn’t check out",
    body: "The reply wasn’t signed by this wearable’s security chip, so it was refused.",
  },
  unsigned: {
    title: "Reply wasn’t signed",
    body: "Signatures are required and the wearable didn’t sign this approval.",
  },
};

export const SIGNATURE_LABELS = {
  verified: { text: "Verified by the security chip", tone: "ok" },
  simulated: { text: "Simulator, not signed", tone: "muted" },
  missing: { text: "Not signed", tone: "warn" },
  invalid: { text: "Invalid, refused", tone: "bad" },
};

export const END_REASONS = {
  removed: "You took the wearable off, so the site locked itself.",
  no_heartbeat: "The wearable stopped reading a heartbeat, so the site locked itself.",
  signal_lost: "The wearable stopped responding, so the site locked itself.",
  locked: "You locked the site.",
  expired: "The session reached its time limit.",
};

export const SIM_OPTIONS = [
  { value: "authorized", label: "Approve" },
  { value: "not_present", label: "Fail: not worn" },
  { value: "no_heartbeat", label: "Fail: no heartbeat" },
  { value: "wrong_finger", label: "Fail: wrong finger" },
  { value: "timeout", label: "Time out" },
];

/* ------------------------------------------------------------------ */
/* Formatters and hooks                                                */
/* ------------------------------------------------------------------ */

const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
export const formatUsd = (n) => usd.format(Number(n) || 0);
export const shortRef = (id) => (id ? id.slice(0, 8).toUpperCase() : "");
export const formatTime = (iso) =>
  iso ? new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" }) : "";
export const formatDuration = (s) => {
  const m = Math.floor(s / 60);
  const sec = String(s % 60).padStart(2, "0");
  return `${m}:${sec}`;
};

export function useCountdown(expiresAt) {
  const calc = () => (expiresAt ? Math.max(0, Math.ceil((new Date(expiresAt) - Date.now()) / 1000)) : 0);
  const [left, setLeft] = useState(calc);
  useEffect(() => {
    setLeft(calc());
    const t = setInterval(() => setLeft(calc()), 250);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expiresAt]);
  return left;
}

/* ------------------------------------------------------------------ */
/* Pieces                                                              */
/* ------------------------------------------------------------------ */

export function CheckRow({ check, state, done }) {
  const { Icon } = check;
  const label =
    state === "pass"
      ? "Passed"
      : state === "fail"
      ? "Failed"
      : state === "active"
      ? "Checking"
      : done
      ? "Not reached"
      : "Waiting";
  return (
    <li className={`check is-${state || "waiting"}`}>
      <span className="check-icon">
        <Icon />
      </span>
      <span className="check-text">
        <span className="check-title">{check.title}</span>
        {!done && <span className="check-detail">{check.detail}</span>}
      </span>
      <span className="check-state">{label}</span>
    </li>
  );
}

/** Live check list: the first waiting check is shown as "Checking". */
export function LiveChecks({ checks }) {
  const activeKey = CHECKS.find((c) => checks?.[c.key] === "waiting")?.key;
  return (
    <ol className="checks">
      {CHECKS.map((c) => (
        <CheckRow
          key={c.key}
          check={c}
          state={checks?.[c.key] === "waiting" && c.key === activeKey ? "active" : checks?.[c.key]}
        />
      ))}
    </ol>
  );
}

export function Countdown({ expiresAt, total }) {
  const remaining = useCountdown(expiresAt);
  const pct = Math.max(0, Math.min(100, (remaining / total) * 100));
  return (
    <div className="timer" aria-label={`${remaining} seconds left`}>
      <div className="timer-bar">
        <span style={{ width: `${pct}%` }} />
      </div>
      <span className="timer-text">{remaining}s left</span>
    </div>
  );
}

/** The signature element: an ECG strip. idle | beating | ok | bad. bpm sets the sweep speed. */
export function EcgStrip({ state, bpm }) {
  const beatPath = useMemo(() => buildBeats(4, 150), []);
  const path = state === "bad" ? "M0 32 L600 32" : beatPath;
  const style = bpm ? { "--sweep": `${((4 * 60) / bpm).toFixed(2)}s` } : undefined;
  return (
    <div className={`strip is-${state}`} aria-hidden="true" style={style}>
      <svg viewBox="0 0 600 64" preserveAspectRatio="none">
        <path className="trace-base" d={path} pathLength="1" />
        <path className="trace-sweep" d={path} pathLength="1" />
      </svg>
    </div>
  );
}

function buildBeats(count, width) {
  let d = "M0 32";
  for (let i = 0; i < count; i++) {
    const x = i * width;
    d +=
      ` L${x + 40} 32` +
      ` Q${x + 46} 32 ${x + 49} 27 Q${x + 52} 22 ${x + 55} 32` +
      ` L${x + 64} 32 L${x + 68} 38 L${x + 74} 6 L${x + 80} 56 L${x + 85} 32` +
      ` L${x + 98} 32 Q${x + 106} 32 ${x + 110} 23 Q${x + 115} 15 ${x + 121} 32` +
      ` L${x + width} 32`;
  }
  return d;
}

/** Cute heart with a face. mood: happy | sad. Happy hearts beat at the wearer's bpm. */
export function HeartFace({ mood, size = 120, bpm }) {
  const style = { width: size, height: size * 0.92 };
  if (mood === "happy" && bpm) style["--beat"] = `${(60 / bpm).toFixed(2)}s`;
  return (
    <svg className={`heartface is-${mood}`} viewBox="0 0 120 110" style={style} aria-hidden="true">
      <path
        className="hf-body"
        d="M60 104S6 72 6 36A27 27 0 0 1 60 22a27 27 0 0 1 54 14c0 36-54 68-54 68z"
      />
      {mood === "happy" ? (
        <>
          <circle cx="44" cy="46" r="5" className="hf-ink" />
          <circle cx="76" cy="46" r="5" className="hf-ink" />
          <path d="M46 62q14 14 28 0" className="hf-line" />
          <circle cx="34" cy="60" r="5" className="hf-blush" />
          <circle cx="86" cy="60" r="5" className="hf-blush" />
        </>
      ) : (
        <>
          <path d="M60 22l-5 9 6 5-4 7" className="hf-crack" />
          <path d="M38 44l10 4M82 44l-10 4" className="hf-line" />
          <path d="M47 70q13-11 26 0" className="hf-line" />
          <path d="M40 54q-3 8 0 10q3-2 0-10z" className="hf-tear" />
        </>
      )}
    </svg>
  );
}

/* ------------------------------------------------------------------ */
/* Icons                                                               */
/* ------------------------------------------------------------------ */

export function LogoMark() {
  return (
    <svg className="logo" viewBox="0 0 32 32" aria-hidden="true">
      <rect width="32" height="32" rx="9" />
      <path d="M4 17h6l2-5 4 11 3-8 2 2h7" />
    </svg>
  );
}

function TouchIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="12" cy="12" r="3.2" />
      <circle cx="12" cy="12" r="7" opacity=".55" />
      <circle cx="12" cy="12" r="10.4" opacity=".25" />
    </svg>
  );
}

function HeartIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 20s-7.5-4.6-7.5-10.2A4.3 4.3 0 0 1 12 7.3a4.3 4.3 0 0 1 7.5 2.5C19.5 15.4 12 20 12 20z" />
    </svg>
  );
}

function FingerprintIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M6.5 9.5a5.5 5.5 0 0 1 11 0v2.5" />
      <path d="M9 19c.8-1.6 1.2-3.5 1.2-5.5v-3a1.8 1.8 0 0 1 3.6 0v3c0 2.6-.5 4.8-1.4 6.6" />
      <path d="M4.5 14.5c.4-1.2.6-2.6.6-4A6.9 6.9 0 0 1 12 3.6a6.9 6.9 0 0 1 6.9 6.9v1.5c0 2.4-.4 4.6-1.2 6.5" />
      <path d="M15.8 15.5c-.2 1.4-.6 2.8-1.2 4" />
    </svg>
  );
}

export function CheckGlyph() {
  return (
    <svg viewBox="0 0 48 48">
      <path d="M14 25l7 7 14-15" />
    </svg>
  );
}

export function CrossGlyph() {
  return (
    <svg viewBox="0 0 48 48">
      <path d="M16 16l16 16M32 16L16 32" />
    </svg>
  );
}
