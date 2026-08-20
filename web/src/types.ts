export type Lane = "tap" | "voice";
export type Kind = "bool" | "tri" | "multi" | "single" | "text" | "longtext" | "int" | "date";
export type AnswerStatus = "answered" | "unknown" | "skipped" | "needs_review";

export interface Option { id: string; label: string }

export interface Question {
  id: string;
  chapter: string;
  group: string;
  prompt: string;
  kind: Kind;
  lane: Lane;
  why: string;
  legal: string;
  explain: string;
  example: string;
  needs: string;
  allowsUnknown: boolean;
  options: Option[];
  dependsOn: string | null;
  dependsOnIds: string[];
}

export interface Chapter {
  id: string;
  title: string;
  blurb: string;
  minutes: number;
  audience: "seller" | "agent";
}

export interface FormSpec {
  formType: string;
  title: string;
  chapters: Chapter[];
  questions: Question[];
  rationale: Record<string, string>;
}

export interface AnswerRecord {
  value: unknown;
  status: AnswerStatus;
  source: "form" | "voice" | "agent" | "system";
  revision: number;
}

export interface ChapterProgress {
  id: string; title: string; blurb: string;
  total: number; answered: number; complete: boolean;
}

export interface Progress {
  chapters: ChapterProgress[];
  answered: number; total: number; percent: number; minutes_left: number;
}

export interface Flag {
  id: string; ruleId: string; severity: "soft" | "hard";
  questionIds: string[]; message: string; prompt: string;
}

export interface SellerState {
  property: { address: string; city: string; county: string };
  sellerName: string;
  agentName: string;
  status: string;
  cursor: string | null;
  answers: Record<string, AnswerRecord>;
  progress: Progress;
  flags: Flag[];
  missingRequired: string[];
}

export interface SellerBootstrap extends SellerState { form: FormSpec }

export interface Deal {
  id: string;
  property_address: string; city: string; county: string;
  seller_name: string; seller_email: string; co_seller_name: string;
  created_at: string;
  session_id: string | null;
  status: string;
  percent: number;
  open_flags: number;
  link_issued: boolean;
  link_last_used: string | null;
  submitted_at: string | null;
}

export interface AgentUser { id: string; email: string; name: string; brokerage: string }
