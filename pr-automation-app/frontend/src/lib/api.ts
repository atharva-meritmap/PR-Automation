const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export interface UserMe {
  id: string;
  email: string;
  full_name: string;
  role: "admin" | "officer";
  is_active: boolean;
}

export interface ClientKeyFacts {
  visa_category?: string;
  role_title?: string;
  company?: string;
  achievements?: string;
  [key: string]: unknown;
}

export interface ClientItem {
  id: string;
  officer_id: string;
  name: string;
  domain: string;
  industry: string;
  brand_voice: string;
  key_facts: ClientKeyFacts;
}

export interface ArticleItem {
  id: string;
  title: string;
  url: string;
  published_at: string | null;
  summary: string;
  outlet: string;
  source_url: string;
  scraped_at: string;
}

export interface JournalistItem {
  id: string;
  canonical_name: string;
  outlet: string;
  beat: string;
  email: string | null;
  email_status: string;
  profile_source_url: string | null;
  profile_scraped_at: string | null;
  confidence_score: number;
  articles: ArticleItem[];
}

export interface CuratedJournalistMatch {
  journalist_id: string;
  canonical_name: string;
  outlet: string;
  beat: string;
  email: string | null;
  email_status: string;
  is_relevant: boolean;
  confidence: number;
  matched_article_title: string | null;
  matched_article_url: string | null;
  in_cooldown: boolean;
  suppressed: boolean;
}

export async function apiFetch<T>(
  path: string,
  options: RequestInit = {},
  token?: string | null,
): Promise<T> {
  const send = async (bearer: string | null | undefined) => {
    const headers = new Headers(options.headers);
    headers.set("Content-Type", "application/json");
    if (bearer) {
      headers.set("Authorization", `Bearer ${bearer}`);
    }
    return fetch(`${API_URL}${path}`, { ...options, headers });
  };

  let res = await send(token);

  if (res.status === 401 && typeof window !== "undefined") {
    const refreshToken = window.localStorage.getItem("pr_refresh_token");
    if (refreshToken && !path.includes("/auth/refresh")) {
      const refRes = await fetch(`${API_URL}/api/v1/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refreshToken }),
      });
      if (refRes.ok) {
        const pair = (await refRes.json()) as { access_token: string; refresh_token: string };
        window.localStorage.setItem("pr_access_token", pair.access_token);
        window.localStorage.setItem("pr_refresh_token", pair.refresh_token);
        res = await send(pair.access_token);
      }
    }
  }

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(typeof err.detail === "string" ? err.detail : "Request failed");
  }
  if (res.status === 204) {
    return undefined as T;
  }
  return (await res.json()) as T;
}

export interface QwotedStatus {
  connected: boolean;
  email: string | null;
}

export interface PrOpportunityItem {
  id: string;
  client_id: string;
  source_platform: string;
  external_id: string;
  title: string;
  summary: string;
  publication: string;
  journalist_name: string;
  deadline: string;
  share_url: string;
  hashtags: string[];
  is_relevant: boolean;
  confidence: number;
  pitch_status: string;
  draft_pitch: string;
  scraped_at: string;
}

export interface PitchItem {
  id: string;
  client_id: string;
  officer_id: string;
  journalist_id: string;
  cited_article_id: string;
  tone: string;
  subject: string;
  body: string;
  grounding_claims: Record<string, unknown>;
  status: string;
  approved_by: string | null;
  approved_at: string | null;
  created_at: string;
  journalist_name: string;
  journalist_email: string | null;
  journalist_email_status: string;
  journalist_outlet: string;
  cited_article_title: string;
  cited_article_url: string;
}

export interface ReplyClassificationResult {
  pitch_id: string;
  intent: string;
  confidence: number;
  suppressed: boolean;
  pitch_status: string;
  detail: string;
}

export interface FollowUpItem {
  id: string;
  client_id: string;
  officer_id: string;
  journalist_id: string;
  pitch_id: string | null;
  journalist_name: string;
  journalist_outlet: string;
  journalist_email: string | null;
  pitch_subject: string | null;
  due_at: string;
  status: string;
  note: string;
  is_overdue: boolean;
  completed_at: string | null;
  created_at: string;
}


