import { useEffect, useState } from "react";
import { SECRET_PAGE_URL, api } from "./api.js";
import { ReceiptCheck } from "./Payments.jsx";
import {
  CHECKS,
  CheckRow,
  Countdown,
  END_REASONS,
  EcgStrip,
  HeartFace,
  LiveChecks,
  REASONS,
  SIGNATURE_LABELS,
  formatDuration,
  formatTime,
} from "./shared.jsx";

export default function SecureSite({ session, health, pendingClaim, onUnlock, onCancel, onLock, busy, error }) {
  if (!session) {
    return (
      <div className="layout">
        <section className="panel flow">
          <EcgStrip state="idle" />
          <div className="flow-body">
            <p className="lede">Connecting to the backend…</p>
          </div>
        </section>
      </div>
    );
  }

  const mine =
    pendingClaim &&
    ((session.status === "unlocking" && session.request?.id === pendingClaim.request_id) ||
      (session.status === "active" && session.session?.request_id === pendingClaim.request_id));

  let view = "forbidden";
  if (session.has_access) view = "granted";
  else if (mine) view = "unlocking";

  const bpm = session.wearable?.bpm || null;
  const lastEvent = latestEvent(session);
  const stripState =
    view === "granted" ? "beating" : view === "unlocking" ? "beating" : lastEvent ? "bad" : "idle";

  return (
    <div className="layout">
      <section className={`panel flow site is-${view}`} aria-live="polite">
        <EcgStrip state={stripState} bpm={view === "granted" ? bpm : null} />
        <div className="flow-body" key={view}>
          {error && (
            <p className="banner" role="alert">
              {error}
            </p>
          )}
          {view === "forbidden" && (
            <Forbidden session={session} lastEvent={lastEvent} onUnlock={onUnlock} busy={busy} />
          )}
          {view === "unlocking" && (
            <Unlocking session={session} timeout={health?.timeout_seconds ?? 30} onCancel={onCancel} />
          )}
          {view === "granted" && <Granted session={session} onLock={onLock} busy={busy} />}
        </div>
      </section>

      <aside className="panel side">
        <div className="side-head">
          <h2>Wearable</h2>
          <span className={`count ${session.wearable?.worn && session.wearable?.live ? "is-live" : ""}`}>
            {session.continuous_check ? "Continuous check on" : "Continuous check off"}
          </span>
        </div>
        <WearableReadout wearable={session.wearable} />

        <div className="raw-link">
          <p>
            The same rule protects a plain server page. Without an unlocked session it answers with a real HTTP 403.
          </p>
          <a className="btn ghost wide small" href={SECRET_PAGE_URL} target="_blank" rel="noreferrer">
            Open /secret in a new tab
          </a>
        </div>

        <ReceiptCheck storageKind={health?.storage?.kind} hasItems />
      </aside>
    </div>
  );
}

/** The most recent thing that ended access: a failed unlock or a closed session. */
function latestEvent(session) {
  const f = session.last_failure;
  const e = session.last_end;
  if (f && (!e || f.at > e.at)) return { type: "failure", ...f };
  if (e) return { type: "end", ...e };
  return null;
}

function Forbidden({ session, lastEvent, onUnlock, busy }) {
  const otherBrowser = session.status === "active" && !session.has_access;
  let note = null;
  if (otherBrowser) {
    note = "The wearable unlocked a session in another browser. Lock it there first.";
  } else if (session.status === "unlocking") {
    note = "Someone else is unlocking right now.";
  } else if (lastEvent?.type === "end") {
    note = END_REASONS[lastEvent.reason] || "The session ended.";
  } else if (lastEvent?.type === "failure") {
    const r = REASONS[lastEvent.reason] || REASONS.device_error;
    note = `${r.title}. ${r.body}`;
  }

  return (
    <div className="gate">
      <HeartFace mood="sad" size={128} />
      <p className="code-403" aria-label="Error 403">
        403
      </p>
      <h1 className="title">No access</h1>
      <p className="lede center">This page only opens while you’re wearing your Pulse device.</p>
      {note && <p className="gate-note">{note}</p>}

      {lastEvent?.type === "failure" && lastEvent.checks && (
        <ol className="checks compact">
          {CHECKS.map((c) => (
            <CheckRow key={c.key} check={c} state={lastEvent.checks[c.key]} done />
          ))}
        </ol>
      )}

      <button className="btn primary wide" onClick={onUnlock} disabled={busy || otherBrowser || session.status === "unlocking"}>
        {busy ? "Asking the wearable…" : "Unlock with Pulse"}
      </button>
    </div>
  );
}

function Unlocking({ session, timeout, onCancel }) {
  const checks = session.request?.checks || { presence: "pass", liveness: "pass", identity: "pass" };
  return (
    <div className="approving">
      <h1 className="title">Unlock on your wearable</h1>
      <p className="lede">Keep the band on and place your enrolled finger on the sensor.</p>
      <LiveChecks checks={checks} />
      {session.request ? (
        <Countdown expiresAt={session.request.expires_at} total={timeout} />
      ) : (
        <p className="lede">Opening the site…</p>
      )}
      <button className="btn ghost wide" onClick={onCancel}>
        Cancel
      </button>
    </div>
  );
}

function Granted({ session, onLock, busy }) {
  const [vault, setVault] = useState(null);
  const [vaultError, setVaultError] = useState("");
  const w = session.wearable || {};
  const bpm = w.bpm || null;

  useEffect(() => {
    let alive = true;
    api
      .vault()
      .then((v) => alive && setVault(v))
      .catch((err) => alive && setVaultError(err.status === 403 ? "" : err.message));
    return () => {
      alive = false;
    };
  }, [session.session?.session_id]);

  return (
    <div className="gate granted">
      <HeartFace mood="happy" size={128} bpm={bpm} />
      <h1 className="title hello">{vault?.message || "Hello world, you’re in."}</h1>
      <p className="lede center">
        Wearable on, heart at <strong>{bpm ? `${bpm} bpm` : "reading…"}</strong>. Take it off and this page locks itself.
      </p>

      <div className="session-meta">
        <span>Unlocked at {formatTime(session.session?.started_at)}</span>
        <span>Open for {formatDuration(session.session?.seconds || 0)}</span>
        {session.session?.signature && (
          <span className={`sig is-${SIGNATURE_LABELS[session.session.signature]?.tone || "muted"}`}>
            {SIGNATURE_LABELS[session.session.signature]?.text}
          </span>
        )}
      </div>

      {vaultError && <p className="banner">{vaultError}</p>}
      {vault && (
        <div className="vault">
          <div className="vault-head">
            <h2>{vault.project}</h2>
            <span>{vault.issues.length} issues</span>
          </div>
          <ul>
            {vault.issues.map((i) => (
              <li key={i.key}>
                <span className="v-key">{i.key}</span>
                <span className="v-title">{i.title}</span>
                <span className={`v-state is-${i.state.toLowerCase().replace(/\s+/g, "-")}`}>{i.state}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <button className="btn ghost wide" onClick={onLock} disabled={busy}>
        Lock now
      </button>
    </div>
  );
}

function WearableReadout({ wearable }) {
  const w = wearable || {};
  const stale = w.signal_age == null || w.signal_age > 4;
  const rows = [
    { label: "Worn", value: stale ? "No signal" : w.worn ? "Yes" : "No", ok: !stale && w.worn },
    {
      label: "Heartbeat",
      value: stale ? "No signal" : w.worn && w.live && w.bpm ? `${w.bpm} bpm` : "None",
      ok: !stale && w.worn && w.live,
    },
    {
      label: "Last reading",
      value: w.signal_age == null ? "Never" : `${w.signal_age < 1 ? "just now" : `${Math.round(w.signal_age)}s ago`}`,
      ok: !stale,
    },
  ];
  return (
    <dl className="readout">
      {rows.map((r) => (
        <div key={r.label}>
          <dt>{r.label}</dt>
          <dd className={r.ok ? "is-ok" : "is-bad"}>{r.value}</dd>
        </div>
      ))}
    </dl>
  );
}
