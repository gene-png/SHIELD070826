/** Wire types mirroring apps/api/app/schemas/tech_debt.py. */

export type ServiceKind =
  | "tech_debt"
  | "zero_trust_cisa"
  | "zero_trust_dod"
  | "nist_csf"
  | "attack_coverage";

export type ServiceStatus =
  "draft" | "in_progress" | "review" | "released" | "archived";

export type CapabilityListStatus = "draft" | "approved" | "released";

export type CapabilityDisposition = "keep" | "consolidate" | "cut";

export interface ServiceResponse {
  id: string;
  kind: ServiceKind;
  status: ServiceStatus;
  title: string;
  source_request_id: string | null;
  opened_by: string;
  released_at: string | null;
  created_at: string;
}

export interface CapabilityItem {
  id: string;
  capability_list_id: string;
  name: string;
  vendor: string | null;
  category: string | null;
  function: string | null;
  annual_cost_usd: number | null;
  license_count: number | null;
  notes: string | null;
  confidence_pct: number | null;
  source_artifact_id: string | null;
  disposition: CapabilityDisposition | null;
  disposition_rationale: string | null;
  consolidation_target_id: string | null;
}

export interface CapabilityList {
  id: string;
  service_id: string;
  version: number;
  status: CapabilityListStatus;
  items: CapabilityItem[];
  approved_at: string | null;
  approved_by: string | null;
}

export interface CapabilityItemPatch {
  name?: string;
  vendor?: string;
  category?: string;
  function?: string;
  annual_cost_usd?: number | null;
  license_count?: number | null;
  notes?: string;
  disposition?: CapabilityDisposition | null;
  disposition_rationale?: string;
  consolidation_target_id?: string | null;
}

export interface ConsolidationPlanSummary {
  capability_list_id: string;
  capability_list_version: number;
  total_items: number;
  keep_count: number;
  consolidate_count: number;
  cut_count: number;
  undecided_count: number;
  estimated_annual_savings: number;
  savings_cost_known: boolean;
}

export interface OverlapBucket {
  key: string;
  item_count: number;
  total_cost: number;
  cost_known: boolean;
  item_ids: string[];
  item_names: string[];
}

export interface TopCostItem {
  id: string;
  name: string;
  vendor: string | null;
  category: string | null;
  annual_cost_usd: number;
}

export interface OverlapAnalysis {
  capability_list_id: string;
  capability_list_version: number;
  by_category: OverlapBucket[];
  by_vendor: OverlapBucket[];
  top_cost_items: TopCostItem[];
  total_cost: number;
  total_items: number;
  uncategorized_count: number;
  no_vendor_count: number;
  no_cost_count: number;
}

export interface Deliverable {
  id: string;
  service_id: string;
  title: string;
  summary: string | null;
  version: number;
  pdf_artifact_id: string | null;
  xlsx_artifact_id: string | null;
  docx_artifact_id: string | null;
  html_artifact_id: string | null;
  pdf_filename: string | null;
  xlsx_filename: string | null;
  docx_filename: string | null;
  html_filename: string | null;
  finalized_at: string | null;
  finalized_by: string | null;
  released_to_client_at: string | null;
  superseded_by: string | null;
}
