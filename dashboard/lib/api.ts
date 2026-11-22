export const API = process.env.NEXT_PUBLIC_SKILLFORGE_API ?? "http://localhost:8000";

export async function get<T>(path: string): Promise<T> {
  const r = await fetch(`${API}${path}`, { cache: "no-store" });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json() as Promise<T>;
}

export async function getText(path: string): Promise<string> {
  const r = await fetch(`${API}${path}`, { cache: "no-store" });
  if (!r.ok) throw new Error(`${r.status}`);
  return r.text();
}

export async function post<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(`${API}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json() as Promise<T>;
}

export type RunRow = {
  id: string;
  created_at: number;
  request: string;
  status: string;
  provider: string;
  holdout_opened: number;
  error: string | null;
  summary: null | {
    recommended: { id: string; label: string; architecture: string };
    holdout_pass_rate: number;
    holdout_pass_ci: [number, number];
    hard_violations: Record<string, number>;
    acceptance_passed: boolean;
    spend_usd: number;
    simulated: boolean;
    stop_reason: string;
  };
};

export type Gen = {
  generation: number;
  population: number;
  feasible: number;
  pareto_size: number;
  best_pass_rate: number;
  best_feasible_pass_rate: number;
  mean_pass_rate: number;
  architectures: Record<string, number>;
  train_pool_size: number;
  adversarial_added: number;
  spend_usd: number;
  epsilon: number;
  restarts: number;
  front: { id: string; label: string; feasible: boolean; pass_rate: number; cost_per_item_usd: number }[];
};

export type Metrics = Record<string, number>;

export type Candidate = {
  id: string;
  generation: number;
  parents: string[];
  label: string;
  architecture: string;
  origin: string;
  mutation_log: string[];
  complexity: number;
  status: string;
  rejection_reason: string | null;
  train: Metrics | null;
  train_feasible: boolean | null;
  validation: Metrics | null;
  holdout: Metrics | null;
};

export type EventRow = { id: number; ts: number; kind: string; message: string };
