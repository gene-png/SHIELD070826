"use client";

import * as React from "react";

import {
  Card,
  CardBody,
  CardDescription,
  CardHeader,
  CardTitle,
  DataTable,
  EmptyState,
  NumberCard,
  type DataTableColumn,
} from "@shield/design-system";

import {
  describeRiskError,
  exportRiskRegister,
  fetchRiskGate,
  fetchRiskRegisterLatest,
  generateRiskRegister,
  getActiveClientId,
  getClientName,
} from "@/lib/risk/client";
import {
  IMPACTS,
  LIKELIHOODS,
  TIER_COLOR,
  isImpact,
  isLikelihood,
  tierFor,
  titleCase,
  type RiskTier,
} from "@/lib/risk/matrix";
import type { RiskEntry, RiskGate, RiskRegister } from "@/lib/risk/types";
import { ClientSwitcher } from "@/components/site/ClientSwitcher";

function TierChip({ tier }: { tier: string | null }): JSX.Element {
  const t = (tier ?? "negligible") as RiskTier;
  const color = TIER_COLOR[t] ?? TIER_COLOR.negligible;
  return (
    <span
      className="inline-block rounded-full px-2 py-0.5 text-xs font-semibold"
      style={{ backgroundColor: color.bg, color: color.fg }}
    >
      {titleCase(tier)}
    </span>
  );
}

function Matrix({ entries }: { entries: RiskEntry[] }): JSX.Element {
  const counts = new Map<string, number>();
  for (const e of entries) {
    if (isLikelihood(e.likelihood) && isImpact(e.impact)) {
      const key = `${e.likelihood}|${e.impact}`;
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
  }
  const rows = [...LIKELIHOODS].reverse();
  return (
    <div className="overflow-x-auto">
      <table className="border-collapse text-center text-xs">
        <thead>
          <tr>
            <th className="p-2" />
            {IMPACTS.map((im) => (
              <th key={im} className="p-2 font-medium text-ink-secondary">
                {titleCase(im)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((lk) => (
            <tr key={lk}>
              <th className="whitespace-nowrap p-2 text-right font-medium text-ink-secondary">
                {titleCase(lk)}
              </th>
              {IMPACTS.map((im) => {
                const color = TIER_COLOR[tierFor(lk, im)];
                const n = counts.get(`${lk}|${im}`) ?? 0;
                return (
                  <td
                    key={im}
                    className="h-12 w-16 border border-white text-sm font-semibold"
                    style={{ backgroundColor: color.bg, color: color.fg }}
                    title={`${titleCase(lk)} × ${titleCase(im)}`}
                  >
                    {n > 0 ? n : ""}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const COLUMNS: DataTableColumn<RiskEntry>[] = [
  { key: "title", header: "Weakness", cell: (r) => r.title },
  { key: "axis", header: "Axis", cell: (r) => titleCase(r.axis) },
  {
    key: "li",
    header: "Likelihood × Impact",
    cell: (r) => `${titleCase(r.likelihood)} × ${titleCase(r.impact)}`,
  },
  { key: "tier", header: "Tier", cell: (r) => <TierChip tier={r.tier} /> },
  {
    key: "action",
    header: "Recommended",
    cell: (r) => titleCase(r.recommended_action),
  },
  {
    key: "source",
    header: "Source",
    cell: (r) => r.source_id ?? "—",
  },
];

function DownloadLink({
  id,
  filename,
  label,
}: {
  id: string | null;
  filename: string | null;
  label: string;
}): JSX.Element | null {
  if (!id) return null;
  return (
    <a
      href={`/api/proxy/artifacts/${id}/download`}
      className="rounded-md border border-border-default px-3 py-1.5 text-sm font-medium text-ink-primary hover:bg-surface-muted"
    >
      {label}
      {filename ? (
        <span className="ml-1 text-ink-tertiary">({filename})</span>
      ) : null}
    </a>
  );
}

export function RiskRegisterDashboard(): JSX.Element {
  const [cid, setCid] = React.useState<string | null>(null);
  const [clientName, setClientName] = React.useState("Client");
  const [gate, setGate] = React.useState<RiskGate | null>(null);
  const [register, setRegister] = React.useState<RiskRegister | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [busy, setBusy] = React.useState<"generate" | "export" | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    let active = true;
    (async () => {
      try {
        const id = await getActiveClientId();
        if (!active) return;
        setCid(id);
        if (!id) {
          setLoading(false);
          return;
        }
        const [name, g, reg] = await Promise.all([
          getClientName(id),
          fetchRiskGate(id),
          fetchRiskRegisterLatest(id),
        ]);
        if (!active) return;
        setClientName(name);
        setGate(g);
        setRegister(reg);
      } catch (err) {
        if (active) setError(describeRiskError(err));
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => {
      active = false;
    };
  }, []);

  async function onGenerate(): Promise<void> {
    if (!cid) return;
    setBusy("generate");
    setError(null);
    try {
      setRegister(await generateRiskRegister(cid));
    } catch (err) {
      setError(describeRiskError(err));
    } finally {
      setBusy(null);
    }
  }

  async function onExport(): Promise<void> {
    if (!cid) return;
    setBusy("export");
    setError(null);
    try {
      setRegister(await exportRiskRegister(cid));
    } catch (err) {
      setError(describeRiskError(err));
    } finally {
      setBusy(null);
    }
  }

  if (loading) {
    return <p className="text-sm text-ink-secondary">Loading…</p>;
  }

  if (!cid) {
    return (
      <EmptyState
        title="Pick a client first"
        description="The Risk Register is generated per client. Choose one to continue:"
        action={<ClientSwitcher />}
      />
    );
  }

  if (gate && !gate.unlocked) {
    return (
      <EmptyState
        title="Risk Register is locked"
        description={`To synthesise risks for ${clientName}, first complete: ${gate.missing.join("; ")}.`}
      />
    );
  }

  const tc = register?.tier_counts ?? {};
  const ac = register?.axis_counts ?? {};

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-ink-primary">
            Risk Register
          </h1>
          <p className="mt-1 text-sm text-ink-secondary">
            {clientName}
            {register
              ? ` · version ${register.version}`
              : " · not yet generated"}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={onGenerate}
            disabled={busy !== null}
            className="rounded-md bg-brand-500 px-4 py-2 text-sm font-semibold text-ink-on-accent hover:bg-brand-600 disabled:opacity-50"
          >
            {busy === "generate"
              ? "Generating…"
              : register
                ? "Regenerate"
                : "Generate"}
          </button>
          {register ? (
            <button
              type="button"
              onClick={onExport}
              disabled={busy !== null}
              className="rounded-md border border-border-default px-4 py-2 text-sm font-semibold text-ink-primary hover:bg-surface-muted disabled:opacity-50"
            >
              {busy === "export" ? "Exporting…" : "Export XLSX / PDF / Word"}
            </button>
          ) : null}
        </div>
      </div>

      {error ? (
        <p className="rounded-md bg-status-danger-bg px-3 py-2 text-sm text-status-danger-fg">
          {error}
        </p>
      ) : null}

      {!register ? (
        <Card>
          <CardBody>
            <p className="text-sm text-ink-secondary">
              No Risk Register yet. Generate one to synthesise the client&apos;s
              ATT&amp;CK, CSF, and Zero Trust gaps into a tiered register.
            </p>
          </CardBody>
        </Card>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-4 md:grid-cols-5">
            <NumberCard label="Entries" value={register.entries.length} />
            <NumberCard
              label="Critical + High"
              value={(tc.critical ?? 0) + (tc.high ?? 0)}
              deltaTone="negative"
            />
            <NumberCard label="Detection" value={ac.detection ?? 0} />
            <NumberCard label="Prevention" value={ac.prevention ?? 0} />
            <NumberCard label="Response" value={ac.response ?? 0} />
          </div>

          <Card>
            <CardHeader>
              <CardTitle>Likelihood × Impact</CardTitle>
              <CardDescription>
                NIST 800-30 5×5. Each cell counts the entries that land there;
                colour is the derived tier.
              </CardDescription>
            </CardHeader>
            <CardBody>
              <Matrix entries={register.entries} />
            </CardBody>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Register</CardTitle>
              <CardDescription>
                Tier is always code-derived from likelihood × impact. Governance
                columns (owner, approval, review) print blank for the client.
              </CardDescription>
            </CardHeader>
            <CardBody className="flex flex-col gap-4">
              <DataTable
                columns={COLUMNS}
                rows={register.entries}
                rowKey={(r) => r.id}
                emptyState={
                  <p className="p-4 text-sm text-ink-secondary">
                    No entries — the synthesis found no open gaps.
                  </p>
                }
              />
              {register.xlsx_artifact_id ||
              register.pdf_artifact_id ||
              register.docx_artifact_id ? (
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm text-ink-secondary">Downloads:</span>
                  <DownloadLink
                    id={register.xlsx_artifact_id}
                    filename={register.xlsx_filename}
                    label="XLSX"
                  />
                  <DownloadLink
                    id={register.pdf_artifact_id}
                    filename={register.pdf_filename}
                    label="PDF"
                  />
                  <DownloadLink
                    id={register.docx_artifact_id}
                    filename={register.docx_filename}
                    label="Word"
                  />
                </div>
              ) : (
                <p className="text-sm text-ink-tertiary">
                  Export to generate downloadable XLSX / PDF / Word files.
                </p>
              )}
            </CardBody>
          </Card>
        </>
      )}
    </div>
  );
}
