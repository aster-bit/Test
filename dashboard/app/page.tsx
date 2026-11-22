"use client";

import { useEffect, useState } from "react";
import { get, post, type RunRow } from "@/lib/api";

const DEFAULT_REQUEST =
  "Создай максимально надёжный скилл для обработки входящих счетов: извлечь реквизиты, проверить арифметику, " +
  "обнаружить пропущенные, противоречивые и повторные счета, никогда не одобрять некорректный счёт и не выдумывать " +
  "данные. Задержка p95 до 8 s, не дороже $0.02 за документ.";

export default function Home() {
  const [runs, setRuns] = useState<RunRow[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [request, setRequest] = useState(DEFAULT_REQUEST);
  const [provider, setProvider] = useState("auto");
  const [sizes, setSizes] = useState({ train: 60, validation: 80, holdout: 150 });
  const [busy, setBusy] = useState(false);

  const load = () => get<RunRow[]>("/runs").then(setRuns).catch((e) => setErr(String(e)));
  useEffect(() => {
    load();
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, []);

  async function start() {
    setBusy(true);
    try {
      const r = await post<{ run_id: string }>("/runs", { request, provider, ...sizes });
      window.location.href = `/runs/${r.run_id}`;
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <section className="panel">
        <h2>New synthesis run</h2>
        <p className="muted">Budget is unlimited: the run continues until a skill is certified (zero hard violations, all constraints, holdout pass-rate lower 95% bound ≥ target) or you cancel it.</p>
        <textarea rows={4} value={request} onChange={(e) => setRequest(e.target.value)} />
        <div className="row" style={{ marginTop: 10 }}>
          <label>
            Provider
            <select value={provider} onChange={(e) => setProvider(e.target.value)}>
              <option value="auto">auto (Claude Code if installed)</option>
              <option value="claude_code">Claude Code (claude -p)</option>
              <option value="mock">mock (simulated, offline)</option>
            </select>
          </label>
          {(["train", "validation", "holdout"] as const).map((k) => (
            <label key={k}>
              {k}
              <input type="number" value={sizes[k]} onChange={(e) => setSizes({ ...sizes, [k]: Number(e.target.value) })} />
            </label>
          ))}
          <button onClick={start} disabled={busy}>{busy ? "Starting…" : "Start run"}</button>
        </div>
        {err && <p className="bad">{err}</p>}
      </section>

      <section className="panel scroll">
        <h2>Runs</h2>
        <table>
          <thead>
            <tr><th>run</th><th>status</th><th>provider</th><th>recommended</th><th>holdout pass (95% CI)</th><th>hard viol.</th><th>acceptance</th><th>spend</th></tr>
          </thead>
          <tbody>
            {runs.map((r) => (
              <tr key={r.id}>
                <td><a href={`/runs/${r.id}`}>{r.id}</a><div className="muted">{r.request.slice(0, 90)}…</div></td>
                <td><span className={`tag ${r.status === "certified" ? "good" : r.status === "failed" ? "bad" : "warn"}`}>{r.status}</span></td>
                <td>{r.provider}{r.summary?.simulated ? <span className="warn"> (simulated)</span> : null}</td>
                <td>{r.summary?.recommended.architecture ?? "—"}</td>
                <td>{r.summary ? `${r.summary.holdout_pass_rate.toFixed(3)} [${r.summary.holdout_pass_ci.map((x) => x.toFixed(2)).join(", ")}]` : "—"}</td>
                <td>{r.summary ? Object.values(r.summary.hard_violations).reduce((a, b) => a + b, 0) : "—"}</td>
                <td>{r.summary ? (r.summary.acceptance_passed ? <span className="good">pass</span> : <span className="bad">fail</span>) : "—"}</td>
                <td>{r.summary ? `$${r.summary.spend_usd.toFixed(3)}` : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  );
}
