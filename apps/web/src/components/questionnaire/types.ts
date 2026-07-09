/**
 * Generic questionnaire definitions consumed by `QuestionnaireRenderer`.
 *
 * Phase 2 ships the rendering primitive. Phase 4 (CSF) and Phase 5 (ATT&CK)
 * provide concrete definitions, plus any specialized question types that
 * don't fit the generic primitives below.
 *
 * Design goals:
 *   - Definitions are JSON-shaped so they can ship as static assets in
 *     `packages/csf-data` / `packages/attack-data` / `packages/zt-data`.
 *   - Responses are a flat Record keyed by question id, so auto-save can
 *     diff them cheaply and round-trip them through `questionnaire_responses`
 *     (Master Spec §11).
 *   - No domain knowledge in the renderer. CSF's 5-dimension grid will be a
 *     specialized component that composes these primitives.
 */

export type QuestionType =
  | "short_text"
  | "long_text"
  | "number"
  | "score_0_2"
  | "choice"
  | "multi"
  | "yes_no"
  | "tristate";

export interface QuestionChoice {
  value: string;
  label: string;
  description?: string;
}

export interface BaseQuestion {
  /** Stable id used as the key in the `responses` map. */
  id: string;
  prompt: string;
  hint?: string;
  required?: boolean;
}

export interface ShortTextQuestion extends BaseQuestion {
  type: "short_text";
  maxLength?: number;
  placeholder?: string;
}

export interface LongTextQuestion extends BaseQuestion {
  type: "long_text";
  maxLength?: number;
  placeholder?: string;
  /** Suggested rows; the textarea is resizable regardless. */
  rows?: number;
}

export interface NumberQuestion extends BaseQuestion {
  type: "number";
  min?: number;
  max?: number;
  step?: number;
  unit?: string;
}

/**
 * 0-2 score with named labels per Master Spec §11 (CSF 5-dimension
 * scoring uses this primitive five times per subcategory).
 */
export interface ScoreQuestion extends BaseQuestion {
  type: "score_0_2";
  labels?: [string, string, string];
}

export interface ChoiceQuestion extends BaseQuestion {
  type: "choice";
  choices: QuestionChoice[];
}

export interface MultiQuestion extends BaseQuestion {
  type: "multi";
  choices: QuestionChoice[];
}

export interface YesNoQuestion extends BaseQuestion {
  type: "yes_no";
}

export interface TristateQuestion extends BaseQuestion {
  type: "tristate";
  /** Labels for the three states, in the order yes / no / n/a. */
  labels?: [string, string, string];
}

export type Question =
  | ShortTextQuestion
  | LongTextQuestion
  | NumberQuestion
  | ScoreQuestion
  | ChoiceQuestion
  | MultiQuestion
  | YesNoQuestion
  | TristateQuestion;

export interface QuestionnaireSection {
  /** Stable id used in the URL hash and the SectionTabs. */
  id: string;
  title: string;
  description?: string;
  questions: Question[];
}

export interface QuestionnaireDefinition {
  /** Stable id used for analytics + audit-row target_type. */
  id: string;
  title: string;
  /** Short human-readable subtitle - shown under the title. */
  subtitle?: string;
  sections: QuestionnaireSection[];
}

export type ResponseValue =
  string | number | string[] | boolean | "yes" | "no" | "n_a" | null;

export type Responses = Record<string, ResponseValue>;
