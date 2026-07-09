/** Wire types mirroring apps/api/app/schemas/csf.py. */

export type CsfAssessmentStatus =
  "draft" | "submitted" | "approved" | "released";

export interface CatalogSubcategory {
  code: string;
  function: string;
  category: string;
  name: string;
  outcome: string;
  /** Minimum impact profile (LOW/MOD/HIGH) at which this outcome applies. */
  min_profile: string;
}

export interface CatalogCategory {
  code: string;
  function: string;
  name: string;
  purpose: string;
  subcategories: CatalogSubcategory[];
}

export interface CatalogFunction {
  code: string;
  name: string;
  purpose: string;
  categories: CatalogCategory[];
}

export interface CatalogTier {
  tier: number;
  short_label: string;
  description: string;
}

export interface CsfCatalog {
  functions: CatalogFunction[];
  tiers: CatalogTier[];
  total_subcategories: number;
}

export interface CsfAnswer {
  id: string;
  assessment_id: string;
  subcategory_code: string;
  maturity_tier: number | null;
  notes: string | null;
  evidence_artifact_id: string | null;
  answered_by: string | null;
  answered_at: string | null;
}

export interface CsfAssessment {
  id: string;
  service_id: string;
  version: number;
  status: CsfAssessmentStatus;
  approved_at: string | null;
  approved_by: string | null;
  documents_stale?: boolean;
  answers: CsfAnswer[];
  client_target_tier: number | null;
  client_profile: string | null;
}

export interface CsfAnswerPatch {
  maturity_tier?: number | null;
  notes?: string;
  evidence_artifact_id?: string | null;
}

export interface CsfInterviewQuestion {
  external_id: string;
  section_name: string;
  order_index: number;
  stem: string;
  cues: string[];
  /** CSF 2.0 subcategory codes this prompt informs. */
  csf_subcategories: string[];
}

export interface CsfInterviewQuestionnaire {
  framework_key: string;
  profile: string | null;
  questions: CsfInterviewQuestion[];
}

export interface FunctionScore {
  function: string;
  function_name: string;
  subcategory_count: number;
  answered_count: number;
  average_tier: number | null;
  coverage_pct: number;
  weakest_subcategory_codes: string[];
}

export interface CsfScoreSummary {
  assessment_id: string;
  version: number;
  total_subcategories: number;
  answered_subcategories: number;
  coverage_pct: number;
  average_tier: number | null;
  overall_maturity_label: string;
  by_function: FunctionScore[];
}

export interface GapItem {
  code: string;
  function: string;
  function_name: string;
  category: string;
  name: string;
  outcome: string;
  current_tier: number;
  target_tier: number;
  gap_size: number;
  priority_score: number;
  notes: string | null;
}

export interface GapAnalysis {
  assessment_id: string;
  version: number;
  target_tier: number;
  target_label: string;
  total_gap_count: number;
  unscored_count: number;
  gap_count_by_function: Record<string, number>;
  gaps: GapItem[];
}

export interface CsfDeliverable {
  id: string;
  service_id: string;
  title: string;
  summary: string | null;
  version: number;
  pdf_artifact_id: string | null;
  xlsx_artifact_id: string | null;
  pdf_filename: string | null;
  xlsx_filename: string | null;
  finalized_at: string | null;
  finalized_by: string | null;
  released_to_client_at: string | null;
  superseded_by: string | null;
}

// --- Full-Playbook tiered Working Profile (Work Order D4) ---

export interface CsfDimensionScore {
  id: string;
  tier: string;
  subcategory_code: string;
  governance: number;
  policy: number;
  implementation: number;
  monitoring: number;
  improvement: number;
  in_scope: boolean;
  rationale: string | null;
  what_we_found: string | null;
  has_evidence: boolean;
  target_level: number | null;
  locked: boolean;
  total: number;
  level: number;
  evidence_capped: boolean;
}

export interface CsfProfile {
  tier: string;
  rows: CsfDimensionScore[];
}

export interface CsfDimensionScorePatch {
  governance?: number;
  policy?: number;
  implementation?: number;
  monitoring?: number;
  improvement?: number;
  in_scope?: boolean;
  rationale?: string | null;
  what_we_found?: string | null;
  has_evidence?: boolean;
  target_level?: number | null;
  locked?: boolean;
}

export interface EnterpriseSubcategory {
  subcategory_code: string;
  name: string;
  function: string;
  tier_levels: Record<string, number>;
  enterprise_level: number;
  rollup_rule: number;
  target_level: number | null;
  gap: boolean;
  priority: string | null;
}

export interface EnterpriseProfile {
  tiers_in_use: string[];
  subcategories: EnterpriseSubcategory[];
}

export interface CsfDimensionChange {
  tier: string;
  subcategory_code: string;
  field: string;
  old: unknown;
  new: unknown;
}

export interface CsfRunAiResponse {
  changed: CsfDimensionChange[];
  rows: CsfDimensionScore[];
  /** AI execution mode; "fixture" means the output is a deterministic
   * simulation, not a real model call. May be absent on older responses. */
  mode?: "fixture" | "live";
}

export interface ExportedArtifact {
  kind: string;
  label: string;
  artifact_id: string;
  filename: string;
}

export interface CsfPlaybookExport {
  artifacts: ExportedArtifact[];
}
