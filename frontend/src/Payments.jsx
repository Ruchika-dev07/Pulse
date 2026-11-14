import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api.js";
import {
  CHECKS,
  CheckGlyph,
  CheckRow,
  Countdown,
  CrossGlyph,
  EcgStrip,
  LiveChecks,
  REASONS,
  SIGNATURE_LABELS,
  SIM_OPTIONS,
  formatTime,
  formatUsd,
  shortRef,
} from "./shared.jsx";

const QUICK_AMOUNTS = [50, 500, 5000];
const EMPTY_DRAFT = { amount: "", provider: "", requester: "", memo: "" };

export default function Payments({ health, backendDown, simOutcome }) {
  const [view, setView] = useState("form"); // form | confirm | approving | result
  const [draft, setDraft] = useState(EMPTY_DRAFT);
  const [errors, setErrors] = useState({});
  const [banner, setBanner] = useState("");
  const [active, setActive] = useState(null);
  const [history, setHistory] = useState([]);
  const [busy, setBusy] = useState(false);

  const isSim = health?.device?.mode === "sim";
  const timeout = health?.timeout_seconds ?? 30;

  const refreshHistory = useCallback(() => {
    api.listPayments().then(setHistory).catch(() => {});
  }, []);
  useEffect(() => {
    refreshHistory();
    const t = setInterval(refreshHistory, 3000);
    return () => clearInterval(t);
  }, [refreshHistory]);

  // live status of the payment being approved
  useEffect(() => {
    if (view !== "approving" || !active?.id) return;
    let alive = true;
    const t = setInterval(async () => {
      try {
        const p = await api.getPayment(active.id);
        if (!alive) return;
        setActive(p);
        if (p.status !== "pending") {
          setView("result");
          refreshHistory();
        }
      } catch {
        /* keep polling */
      }
    }, 600);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [view, active?.id, refreshHistory]);

  // the receipt is written just after the payment finishes; fetch it once more
  useEffect(() => {
    if (view !== "result" || !active?.id || active.receipt_hash) return;
    let tries = 0;
    const t = setInterval(async () => {
      tries += 1;
      try {
        const p = await api.getPayment(active.id);
        if (p.receipt_hash || tries >= 6) {
          setActive(p);
          clearInterval(t);
        }
      } catch {
        clearInterval(t);
      }
    }, 500);
    return () => clearInterval(t);
  }, [view, active?.id, active?.receipt_hash]);

  const update = (field) => (e) => {
    let value = e.target.value;
    if (field === "amount") {
      value = value.replace(/[^0-9.]/g, "");
      const [whole, ...rest] = value.split(".");
      value = rest.length ? `${whole}.${rest.join("").slice(0, 2)}` : whole;
    }
    setDraft((d) => ({ ...d, [field]: value }));
    setErrors((er) => ({ ...er, [field]: undefined }));
  };

  const validate = () => {
    const er = {};
    const amt = Number(draft.amount);
    if (!draft.amount || !(amt > 0)) er.amount = "Enter an amount above $0.";
    else if (amt > 1_000_000) er.amount = "The limit is $1,000,000 per payment.";
    if (!draft.provider.trim()) er.provider = "Who is this payment going to?";
    if (!draft.requester.trim()) er.requester = "Who is requesting it?";
    setErrors(er);
    return Object.keys(er).length === 0;
  };

  const review = (e) => {
    e.preventDefault();
    setBanner("");
    if (validate()) setView("confirm");
  };

  const send = async (details = draft) => {
    setBusy(true);
    setBanner("");
    try {
      const p = await api.createPayment({
        provider: details.provider.trim(),
        requester: details.requester.trim(),
        amount: Number(details.amount),
        memo: details.memo?.trim() || null,
        ...(isSim ? { sim_outcome: simOutcome } : {}),
      });
      setActive(p);
      setView("approving");
      refreshHistory();
    } catch (err) {
      setBanner(backendDown ? "Can’t reach the backend. Start it with: uvicorn main:app --reload --port 8000" : err.message);
    } finally {
      setBusy(false);
    }
  };

  const cancel = async () => {
    if (!active?.id) return;
    try {
      const p = await api.cancelPayment(active.id);
      setActive(p);
      setView("result");
      refreshHistory();
    } catch (err) {
      setBanner(err.message);
    }
  };

  const retry = () => {
    if (!active) return;
    const details = {
      provider: active.provider,
      requester: active.requester,
      amount: String(active.amount),
      memo: active.memo || "",
    };
    setDraft(details);
    send(details);
  };

  const startOver = () => {
    setActive(null);
    setDraft(EMPTY_DRAFT);
    setErrors({});
    setBanner("");
    setView("form");
  };

  const stripState =
    view === "approving" ? "beating" : view === "result" ? (active?.status === "authorized" ? "ok" : "bad") : "idle";

  return (
    <div className="layout">
      <section className="panel flow" aria-live="polite">
        <EcgStrip state={stripState} />
        <div className="flow-body" key={view}>
          {banner && (
            <p className="banner" role="alert">
              {banner}
            </p>
          )}

          {view === "form" && (
            <PaymentForm draft={draft} errors={errors} update={update} setDraft={setDraft} onSubmit={review} />
          )}

          {view === "confirm" && (
            <ConfirmView
              draft={draft}
              busy={busy}
              onSend={() => send()}
              onEdit={() => setView("form")}
              simLabel={isSim ? SIM_OPTIONS.find((o) => o.value === simOutcome)?.label : null}
            />
          )}

          {view === "approving" && active && (
            <div className="approving">
              <h1 className="title">Approve on your Pulse device</h1>
              <p className="lede">
                The device screen should read <strong>{formatUsd(active.amount)}</strong> to{" "}
                <strong>{active.provider}</strong>.
              </p>
              <LiveChecks checks={active.checks} />
              <Countdown expiresAt={active.expires_at} total={timeout} />
              <button className="btn ghost wide" onClick={cancel}>
                Cancel payment
              </button>
            </div>
          )}

          {view === "result" && active && <ResultView payment={active} onRetry={retry} onNew={startOver} busy={busy} />}
        </div>
      </section>

      <aside className="panel side">
        <div className="side-head">
          <h2>Recent payments</h2>
          <span className="count">{history.length}</span>
        </div>
        <HistoryList items={history} activeId={active?.id} />
        <ReceiptCheck storageKind={health?.storage?.kind} hasItems={history.length > 0} />
      </aside>
    </div>
  );
}

function PaymentForm({ draft, errors, update, setDraft, onSubmit }) {
  const amountRef = useRef(null);
  useEffect(() => amountRef.current?.focus(), []);

  return (
    <form className="form" onSubmit={onSubmit} noValidate>
      <h1 className="title">Send a payment</h1>
      <p className="lede">Nothing moves until the wearer approves it on their Pulse device.</p>

      <div className={`amount-field ${errors.amount ? "has-error" : ""}`}>
        <label htmlFor="amount" className="sr-only">
          Amount in US dollars
        </label>
        <span className="currency" aria-hidden="true">
          $
        </span>
        <input
          id="amount"
          ref={amountRef}
          inputMode="decimal"
          autoComplete="off"
          placeholder="0.00"
          value={draft.amount}
          onChange={update("amount")}
          aria-invalid={!!errors.amount}
          aria-describedby={errors.amount ? "amount-err" : undefined}
        />
      </div>
      {errors.amount && (
        <p className="field-error" id="amount-err">
          {errors.amount}
        </p>
      )}

      <div className="quick" role="group" aria-label="Quick amounts">
        {QUICK_AMOUNTS.map((n) => (
          <button
            type="button"
            key={n}
            className={`chip ${Number(draft.amount) === n ? "is-on" : ""}`}
            onClick={() => setDraft((d) => ({ ...d, amount: String(n) }))}
          >
            {formatUsd(n).replace(".00", "")}
          </button>
        ))}
      </div>

      <Field id="provider" label="Pay to" placeholder="Amazon.com" value={draft.provider} onChange={update("provider")} error={errors.provider} />
      <Field
        id="requester"
        label="Requested by"
        placeholder="Your name or the agent’s name"
        value={draft.requester}
        onChange={update("requester")}
        error={errors.requester}
      />
      <Field id="memo" label="Note" optional placeholder="What it’s for" value={draft.memo} onChange={update("memo")} />

      <button type="submit" className="btn primary wide">
        Review payment
      </button>
    </form>
  );
}

function Field({ id, label, optional, error, ...rest }) {
  return (
    <div className={`field ${error ? "has-error" : ""}`}>
      <label htmlFor={id}>
        {label}
        {optional && <span className="optional"> (optional)</span>}
      </label>
      <input
        id={id}
        autoComplete="off"
        maxLength={60}
        aria-invalid={!!error}
        aria-describedby={error ? `${id}-err` : undefined}
        {...rest}
      />
      {error && (
        <p className="field-error" id={`${id}-err`}>
          {error}
        </p>
      )}
    </div>
  );
}

function ConfirmView({ draft, busy, onSend, onEdit, simLabel }) {
  return (
    <div className="confirm">
      <p className="lede">You’re about to send</p>
      <p className="big-amount">{formatUsd(draft.amount)}</p>
      <p className="to-line">
        to <strong>{draft.provider.trim()}</strong>
      </p>
      <dl className="details">
        <div>
          <dt>Requested by</dt>
          <dd>{draft.requester.trim()}</dd>
        </div>
        {draft.memo.trim() && (
          <div>
            <dt>Note</dt>
            <dd>{draft.memo.trim()}</dd>
          </div>
        )}
        <div>
          <dt>Approval</dt>
          <dd>Touch, heartbeat and fingerprint on the Pulse device</dd>
        </div>
        {simLabel && (
          <div>
            <dt>Simulator</dt>
            <dd className="sim-note">{simLabel}</dd>
          </div>
        )}
      </dl>
      <div className="actions">
        <button className="btn ghost" onClick={onEdit} disabled={busy}>
          Edit
        </button>
        <button className="btn primary" onClick={onSend} disabled={busy}>
          {busy ? "Sending…" : "Send to device"}
        </button>
      </div>
    </div>
  );
}

function ResultView({ payment, onRetry, onNew, busy }) {
  const ok = payment.status === "authorized";
  const reason = REASONS[payment.reason] || REASONS.device_error;
  return (
    <div className={`result ${ok ? "is-ok" : "is-bad"}`}>
      <div className="result-mark" aria-hidden="true">
        {ok ? <CheckGlyph /> : <CrossGlyph />}
      </div>
      <h1 className="title">{ok ? "Payment sent" : reason.title}</h1>
      <p className="lede">
        {ok ? (
          <>
            <strong>{formatUsd(payment.amount)}</strong> to <strong>{payment.provider}</strong>, approved on the Pulse
            device.
          </>
        ) : (
          reason.body
        )}
      </p>

      <ol className="checks compact">
        {CHECKS.map((c) => (
          <CheckRow key={c.key} check={c} state={payment.checks?.[c.key]} done />
        ))}
      </ol>

      <dl className="details">
        <div>
          <dt>Amount</dt>
          <dd>{formatUsd(payment.amount)}</dd>
        </div>
        <div>
          <dt>Pay to</dt>
          <dd>{payment.provider}</dd>
        </div>
        <div>
          <dt>Reference</dt>
          <dd className="ref">{shortRef(payment.id)}</dd>
        </div>
        <div>
          <dt>{ok ? "Approved at" : "Ended at"}</dt>
          <dd>{formatTime(payment.completed_at)}</dd>
        </div>
        {payment.signature && (
          <div>
            <dt>Device signature</dt>
            <dd className={`sig is-${SIGNATURE_LABELS[payment.signature]?.tone || "muted"}`}>
              {SIGNATURE_LABELS[payment.signature]?.text || payment.signature}
            </dd>
          </div>
        )}
        <div>
          <dt>Receipt</dt>
          <dd className="ref" title={payment.receipt_hash || ""}>
            {payment.receipt_hash ? `#${payment.receipt_seq}, ${payment.receipt_hash.slice(0, 12)}` : "Saving…"}
          </dd>
        </div>
      </dl>

      <div className="actions">
        {!ok && (
          <button className="btn ghost" onClick={onRetry} disabled={busy}>
            {busy ? "Sending…" : "Try again"}
          </button>
        )}
        <button className="btn primary" onClick={onNew}>
          New payment
        </button>
      </div>
    </div>
  );
}

function HistoryList({ items, activeId }) {
  if (!items.length) return <p className="empty">Payments you send show up here with their approval result.</p>;
  return (
    <ul className="history-list">
      {items.map((p) => (
        <li key={p.id} className={p.id === activeId ? "is-current" : ""}>
          <div className="h-main">
            <span className="h-provider">{p.provider}</span>
            <span className="h-amount">{formatUsd(p.amount)}</span>
          </div>
          <div className="h-sub">
            <span>
              {p.requester}, {formatTime(p.created_at)}
            </span>
            <StatusBadge status={p.status} />
          </div>
        </li>
      ))}
    </ul>
  );
}

function StatusBadge({ status }) {
  const text = { pending: "Waiting", authorized: "Sent", denied: "Blocked", timeout: "Timed out", cancelled: "Cancelled" }[
    status
  ];
  return <span className={`badge is-${status}`}>{text || status}</span>;
}

export function ReceiptCheck({ storageKind, hasItems }) {
  const [state, setState] = useState(null);
  if (!hasItems) return null;

  const run = async () => {
    setState("checking");
    try {
      setState(await api.verifyReceipts());
    } catch (err) {
      setState({ error: err.message });
    }
  };

  let message = null;
  if (state && state !== "checking") {
    if (state.error) message = <p className="verify-msg is-bad">{state.error}</p>;
    else if (state.ok)
      message = (
        <p className="verify-msg is-ok">
          {state.count === 1 ? "The receipt checks out" : `All ${state.count} receipts check out`}. None were edited or
          removed.
        </p>
      );
    else
      message = (
        <p className="verify-msg is-bad">
          Receipt #{state.broken_at} fails: {state.problem}.
        </p>
      );
  }

  return (
    <div className="verify">
      <button className="btn ghost wide small" onClick={run} disabled={state === "checking"}>
        {state === "checking" ? "Checking receipts…" : "Verify receipt log"}
      </button>
      {message}
      <p className="verify-note">
        Every payment and every unlock is logged with the hash of the entry before it
        {storageKind === "mongodb" ? ", stored in MongoDB Atlas" : ""}. Editing any entry breaks the chain.
      </p>
    </div>
  );
}
