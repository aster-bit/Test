"use client";

import { use, useEffect, useMemo, useState } from "react";
import { get, getText, post, type Candidate, type EventRow, type Gen } from "@/lib/api";

type RunDetail = {
  id: string;
  status: string;
  provider: string;
  request: string;
  error: string | null;
  env: { sandbox: { backend: string; strength: string } } | null;
  summary: { simulated: boolean; stop_reason: string; holdout_pass_rate: number; holdout_pass_ci: [number, number] } | null;
  scenario_counts: { split: string; family: string; origin: string; realism: string; n: number }[];
  artifacts: { path: string; kind: string; sha256: string }[];
  holdout_attempts: { attempt: number; candidate_id: string; passed: number | null; summary: null | { pass_rate: number; pass_ci: [number, number]; unmet?: string[]; report_only?: boolean } }[];
};

const fmt = (v: number | undefined | null, d = 3) => (v === undefined || v === null || Number.isNaN(v) ? "—" : v.toFixed(d));

function LineChart({ gens }: { gens: Gen[] }) {
  const W = 640, H = 220, P = 32;
  if (gens.length === 0) return <p className="muted">no generations yet</p>;
  const xs = (i: number) => P + (i * (W - 2 * P)) / Math.max(1, gens.length - 1);
  const ys = (v: number) => H - P - v * (H - 2 * P);
  const series: [keyof Gen, string, string][] = [
    ["best_pass_rate", "var(--accent)", "best train pass"],
    ["best_feasible_pass_rate", "var(--good)", "best feasible pass"],
    ["mean_pass_rate", "var(--muted)", "mean pass"],
  ];
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="pass rate by generation">
      {[0, 0.25, 0.5, 0.75, 1].map((t) => (
        <g key={t}>
          <line x1={P} x2={W - P} y1={ys(t)} y2={ys(t)} stroke="var(--line)" />
          <text x={4} y={ys(t) + 4}>{t}</text>
        </g>
      ))}
      {gens.map((g, i) => <text key={i} x={xs(i) - 3} y={H - 8}>{g.generation}</text>)}
      {series.map(([k, color, label], si) => (
        <g key={String(k)}>
          <polyline fill="none" stroke={color} strokeWidth={2} points={gens.map((g, i) => `${xs(i)},${ys(g[k] as number)}`).join(" ")} />
          <text x={W - P - 130} y={P + 14 * si} style={{ fill: color }}>{label}</text>
        </g>
      ))}
    </svg>
  );
}

function Scatter({ cands }: { cands: Candidate[] }) {
  const pts = cands.filter((c) => c.train);
  const W = 640, H = 260, P = 36;
  if (!pts.length) return null;
  const maxCost = Math.max(1e-6, ...pts.map((c) => c.train!.cost_per_item_usd));
  const x = (v: number) => P + (Math.log10(1 + (v / maxCost) * 99) / 2) * (W - 2 * P);
  const y = (v: number) => H - P - v * (H - 2 * P);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="pass rate vs cost">
      <text x={P} y={14}>pass rate (train) vs cost per item (log-scaled) — green: feasible, red: violates hard constraints</text>
      <line x1={P} x2={W - P} y1={H - P} y2={H - P} stroke="var(--line)" />
      <line x1={P} x2={P} y1={P} y2={H - P} stroke="var(--line)" />
      {pts.map((c) => (
        <circle key={c.id} cx={x(c.train!.cost_per_item_usd)} cy={y(c.train!.pass_rate)} r={c.status === "recommended" ? 7 : 4}
          fill={c.train_feasible ? "var(--good)" : "var(--bad)"} opacity={c.status === "rejected" ? 0.35 : 0.85}>
          <title>{`${c.id} ${c.architecture}\npass=${fmt(c.train!.pass_rate)} cost=$${c.train!.cost_per_item_usd.toFixed(5)}\n${c.status}`}</title>
        </circle>
      ))}
      <text x={W - P - 60} y={H - 10}>${maxCost.toFixed(4)}</text>
      <text x={P} y={H - 10}>$0</text>
    </svg>
  );
}

export default function RunPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [run, setRun] = useState<RunDetail | null>(null);
  const [gens, setGens] = useState<Gen[]>([]);
  const [cands, setCands] = useState<Candidate[]>([]);
  const [events, setEvents] = useState<EventRow[]>([]);
  const [report, setReport] = useState<string>("");
  const [tab, setTab] = useState<"overview" | "candidates" | "events" | "report">("overview");
  const [sel, setSel] = useState<string | null>(null);
  const [selDetail, setSelDetail] = useState<unknown>(null);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const [r, g, c, e] = await Promise.all([
          get<RunDetail>(`/runs/${id}`), get<Gen[]>(`/runs/${id}/generations`),
          get<Candidate[]>(`/runs/${id}/candidates`), get<EventRow[]>(`/runs/${id}/events`),
        ]);
        if (!alive) return;
        setRun(r); setGens(g); setCands(c); setEvents(e);
        if (r.status === "certified" || r.status === "stopped") getText(`/runs/${id}/report`).then(setReport).catch(() => {});
      } catch { /* API not reachable yet */ }
    };
    load();
    const t = setInterval(load, 3000);
    return () => { alive = false; clearInterval(t); };
  }, [id]);

  useEffect(() => {
    if (sel) get(`/candidates/${sel}`).then(setSelDetail).catch(() => setSelDetail(null));
  }, [sel]);

  const last = gens[gens.length - 1];
  const finalists = useMemo(() => cands.filter((c) => c.validation), [cands]);
  const rejected = cands.filter((c) => c.status === "rejected");
  const reasons = rejected.reduce<Record<string, number>>((a, c) => {
    const k = (c.rejection_reason ?? "?").split(":")[0];
    a[k] = (a[k] ?? 0) + 1;
    return a;
  }, {});

  if (!run) return <p className="muted">loading {id}…</p>;
  return (
    <>
      <h2>{run.id} <span className="tag">{run.status}</span></h2>
      <p className="muted">{run.request}</p>
      {run.summary?.simulated && (
        <div className="banner">LLM stages in this run used a <b>simulated</b> model (mock provider). Their quality, latency and cost figures are modelling assumptions, not measurements.</div>
      )}
      {run.error && <div className="banner bad">{run.error}</div>}
      <div className="tabs">
        {(["overview", "candidates", "events", "report"] as const).map((t) => (
          <button key={t} className={tab === t ? "on" : ""} onClick={() => setTab(t)}>{t}</button>
        ))}
      </div>

      {tab === "overview" && (
        <>
          <section className="panel grid">
            <div className="kpi"><div className="v">{gens.length ? gens.length - 1 : 0}</div><div className="l">generations</div></div>
            <div className="kpi"><div className="v">{cands.length}</div><div className="l">candidates</div></div>
            <div className="kpi"><div className="v">{fmt(last?.best_feasible_pass_rate)}</div><div className="l">best feasible train pass</div></div>
            <div className="kpi"><div className="v">{run.summary ? fmt(run.summary.holdout_pass_rate) : "sealed"}</div><div className="l">holdout pass (exported skill)</div></div>
            <div className="kpi"><div className="v">{last?.restarts ?? 0}</div><div className="l">diversity restarts</div></div>
            <div className="kpi"><div className="v">${fmt(last?.spend_usd, 3)}</div><div className="l">spend {run.provider === "mock" ? "(simulated)" : ""}</div></div>
            <div className="kpi"><div className="v">{run.env?.sandbox.backend ?? "?"}</div><div className="l">{run.env?.sandbox.strength}</div></div>
          </section>
          <section className="panel scroll">
            <h3>Certification attempts {run.status === "running" && <button style={{ float: "right" }} onClick={() => post(`/runs/${id}/cancel`, {}).catch(() => {})}>Stop &amp; export best-so-far</button>}</h3>
            {run.holdout_attempts.length === 0 ? <p className="muted">No candidate has passed the validation gate yet; holdout#0 is sealed.</p> : (
              <table><thead><tr><th>#</th><th>candidate</th><th>holdout pass</th><th>95% CI</th><th>result</th></tr></thead>
                <tbody>{run.holdout_attempts.map((a) => (
                  <tr key={a.attempt}><td>{a.attempt}</td><td>{a.candidate_id}</td><td>{fmt(a.summary?.pass_rate)}</td>
                    <td>{a.summary ? `[${a.summary.pass_ci.map((x) => x.toFixed(3)).join(", ")}]` : "—"}</td>
                    <td className={a.passed ? "good" : "bad"}>{a.passed ? "CERTIFIED" : a.summary?.report_only ? "report only" : a.passed === 0 ? `failed: ${(a.summary?.unmet ?? []).join("; ")}` : "evaluating"}</td></tr>))}
                </tbody></table>)}
          </section>
          <section className="panel"><h3>Search progress</h3><LineChart gens={gens} />
            {last && <p className="muted">ε (tolerated hard-violation rate during search): {fmt(last.epsilon, 4)} · train pool {last.train_pool_size} (adversarial +{last.adversarial_added} last gen)</p>}
          </section>
          <section className="panel"><h3>Candidates: quality vs cost</h3><Scatter cands={cands} /></section>
          <section className="panel scroll">
            <h3>Architectures per generation</h3>
            <table><thead><tr><th>gen</th><th>architectures</th><th>feasible</th><th>front</th></tr></thead>
              <tbody>{gens.map((g) => (
                <tr key={g.generation}><td>{g.generation}</td>
                  <td>{Object.entries(g.architectures).map(([a, n]) => <span key={a} className="tag" style={{ marginRight: 4 }}>{a} ×{n}</span>)}</td>
                  <td>{g.feasible}/{g.population}</td><td>{g.pareto_size}</td></tr>))}
              </tbody></table>
          </section>
          <section className="panel scroll">
            <h3>Finalists (validation → holdout)</h3>
            <table><thead><tr><th>candidate</th><th>arch</th><th>val pass</th><th>val robustness</th><th>val hard-viol. rate</th><th>holdout pass</th><th>cost/item</th><th>status</th></tr></thead>
              <tbody>{finalists.map((c) => (
                <tr key={c.id}><td><a href="#" onClick={(e) => { e.preventDefault(); setSel(c.id); setTab("candidates"); }}>{c.id}</a></td>
                  <td>{c.architecture}</td><td>{fmt(c.validation?.pass_rate)}</td><td>{fmt(c.validation?.robustness)}</td>
                  <td>{fmt(c.validation?.hard_violation_rate)}</td><td>{fmt(c.holdout?.pass_rate)}</td>
                  <td>{fmt(c.validation?.cost_per_item_usd, 5)}</td>
                  <td className={c.status === "recommended" ? "good" : ""}>{c.status}</td></tr>))}
              </tbody></table>
          </section>
          <section className="panel">
            <h3>Rejections</h3>
            <p>{Object.entries(reasons).map(([k, n]) => <span key={k} className="tag" style={{ marginRight: 6 }}>{k}: {n}</span>)}</p>
            <h3>Artifacts</h3>
            <ul>{run.artifacts.map((a) => <li key={a.path}><code>{a.path}</code> <span className="muted">{a.kind} · {a.sha256.slice(0, 12)}</span></li>)}</ul>
          </section>
        </>
      )}

      {tab === "candidates" && (
        <section className="panel scroll">
          <table><thead><tr><th>id</th><th>gen</th><th>arch</th><th>origin</th><th>train pass</th><th>feasible</th><th>cost</th><th>complexity</th><th>status / reason</th></tr></thead>
            <tbody>{cands.map((c) => (
              <tr key={c.id} onClick={() => setSel(c.id)} style={{ cursor: "pointer", background: sel === c.id ? "var(--bg)" : undefined }}>
                <td>{c.id}</td><td>{c.generation}</td><td>{c.architecture}</td><td>{c.origin}</td>
                <td>{fmt(c.train?.pass_rate)}</td><td>{c.train_feasible === null ? "—" : c.train_feasible ? <span className="good">yes</span> : <span className="bad">no</span>}</td>
                <td>{fmt(c.train?.cost_per_item_usd, 5)}</td><td>{fmt(c.complexity, 1)}</td>
                <td>{c.status}{c.rejection_reason ? <div className="muted">{c.rejection_reason}</div> : null}</td></tr>))}
            </tbody></table>
          {sel && (
            <>
              <h3>{sel}</h3>
              <pre>{JSON.stringify(selDetail, null, 2)}</pre>
            </>
          )}
        </section>
      )}

      {tab === "events" && (
        <section className="panel"><pre>{events.map((e) => `${new Date(e.ts * 1000).toLocaleTimeString()}  [${e.kind}] ${e.message}`).join("\n")}</pre></section>
      )}

      {tab === "report" && (
        <section className="panel">{report ? <pre>{report}</pre> : <p className="muted">The report is produced after the single holdout evaluation.</p>}</section>
      )}
    </>
  );
}
