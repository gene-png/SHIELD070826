/** Wire types mirroring apps/api/app/schemas/zt.py. */

export type ZtFramework = "cisa_ztmm_2_0" | "dod_ztra";
export type ZtAssessmentStatus =
  "draft" | "submitted" | "approved" | "released";

export interface CatalogCapability {
  code: string;
  pillar_code: string;
  name: string;
  outcome: string;
}

export interface CatalogPillar {
  code: string;
  name: string;
  purpose: string;
  capabilities: CatalogCapability[];
}

export interface CatalogStage {
  stage: number;
  label: string;
  description: string;
}

export interface ZtCatalog {
  framework: ZtFramework;
  pillars: CatalogPillar[];
  stages: CatalogStage[];
  total_capabilities: number;
}

export interface ZtAnswer {
  id: string;
  assessment_id: string;
  capability_code: string;
  maturity_stage: number | null;
  target_stage?: number | null;
  locked?: boolean;
  notes: string | null;
  evidence_artifact_id: string | null;
  answered_by: string | null;
  answered_at: string | null;
}

export interface ZtAssessment {
  id: string;
  service_id: string;
  framework: ZtFramework;
  version: number;
  status: ZtAssessmentStatus;
  approved_at: string | null;
  approved_by: string | null;
  documents_stale?: boolean;
  answers: ZtAnswer[];
  client_target_stage: number | null;
}

export interface ZtAnswerPatch {
  maturity_stage?: number | null;
  target_stage?: number | null;
  locked?: boolean;
  notes?: string;
  evidence_artifact_id?: string | null;
}

export interface ZtCapabilityChange {
  capability_code: string;
  field: string;
  old: unknown;
  new: unknown;
}

export interface ZtRunAiResponse {
  changed: ZtCapabilityChange[];
  answers: ZtAnswer[];
  pillar_narratives: Record<string, string>;
  executive_summary: string | null;
  roadmap_summary: string | null;
  /** AI execution mode; "fixture" means the output is a deterministic
   * simulation, not a real model call. May be absent on older responses. */
  mode?: "fixture" | "live";
}

export interface PillarScore {
  pillar_code: string;
  pillar_name: string;
  capability_count: number;
  answered_count: number;
  average_stage: number | null;
  coverage_pct: number;
  weakest_capability_codes: string[];
}

export interface ZtScoreSummary {
  assessment_id: string;
  version: number;
  framework: ZtFramework;
  total_capabilities: number;
  answered_capabilities: number;
  coverage_pct: number;
  average_stage: number | null;
  overall_stage_label: string;
  by_pillar: PillarScore[];
}

export interface GapItem {
  code: string;
  pillar_code: string;
  pillar_name: string;
  name: string;
  outcome: string;
  current_stage: number;
  target_stage: number;
  gap_size: number;
  priority_score: number;
  notes: string | null;
}

export interface GapAnalysis {
  assessment_id: string;
  version: number;
  framework: ZtFramework;
  target_stage: number;
  target_label: string;
  total_gap_count: number;
  unscored_count: number;
  gap_count_by_pillar: Record<string, number>;
  gaps: GapItem[];
  roadmap?: RoadmapEntry[];
}

export interface RoadmapEntry {
  month: number;
  code: string;
  pillar_code: string;
  pillar_name: string;
  name: string;
  current_stage: number;
  target_stage: number;
  priority_score: number;
}

export interface ZtDeliverable {
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
