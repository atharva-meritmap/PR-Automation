"use client";

import { useEffect, useState } from "react";
import {
  apiFetch,
  ClientItem,
  CuratedJournalistMatch,
  FollowUpItem,
  JournalistItem,
  PitchItem,
  PrOpportunityItem,
  QwotedStatus,
  ReplyClassificationResult,
  UserMe,
} from "@/lib/api";

type Tab = "clients" | "journalists" | "team";

const STATUS_BADGE: Record<string, string> = {
  verified: "bg-emerald-50 text-emerald-700 border-emerald-200",
  risky: "bg-amber-50 text-amber-700 border-amber-200",
  unverified: "bg-blue-50 text-blue-700 border-blue-200",
  invalid: "bg-red-50 text-red-700 border-red-200",
  missing: "bg-slate-100 text-slate-600 border-slate-200",
};

export default function HomePage() {
  const [token, setToken] = useState<string | null>(null);
  const [user, setUser] = useState<UserMe | null>(null);
  const [tab, setTab] = useState<Tab>("clients");

  const [clients, setClients] = useState<ClientItem[]>([]);
  const [journalists, setJournalists] = useState<JournalistItem[]>([]);
  const [officers, setOfficers] = useState<UserMe[]>([]);

  // Auth form
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  // Client form (individual EB-1A / O-1A / EB-2 NIW candidate)
  const [clientName, setClientName] = useState("");
  const [visaCategory, setVisaCategory] = useState("EB-1A");
  const [roleTitle, setRoleTitle] = useState("");
  const [companyName, setCompanyName] = useState("");
  const [clientDomain, setClientDomain] = useState("");
  const [clientIndustry, setClientIndustry] = useState("");
  const [brandVoice, setBrandVoice] = useState("");

  // Journalist scrape & filter
  const [scrapeUrl, setScrapeUrl] = useState("");
  const [scrapeBeat, setScrapeBeat] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedClientId, setSelectedClientId] = useState<string>("");
  const [curatedClient, setCuratedClient] = useState<ClientItem | null>(null);
  const [curatedMatches, setCuratedMatches] = useState<CuratedJournalistMatch[]>([]);
  const [workspaceSubTab, setWorkspaceSubTab] = useState<"journalists" | "opportunities" | "pitches" | "calendar">("journalists");
  const [opportunities, setOpportunities] = useState<PrOpportunityItem[]>([]);
  const [pitches, setPitches] = useState<PitchItem[]>([]);
  const [followUps, setFollowUps] = useState<FollowUpItem[]>([]);
  const [fuJournalistId, setFuJournalistId] = useState("");
  const [fuDueAt, setFuDueAt] = useState("");
  const [fuNote, setFuNote] = useState("");
  const [pitchSubjectEdits, setPitchSubjectEdits] = useState<Record<string, string>>({});
  const [pitchBodyEdits, setPitchBodyEdits] = useState<Record<string, string>>({});
  const [replyInputs, setReplyInputs] = useState<Record<string, string>>({});
  const [qwotedStatus, setQwotedStatus] = useState<QwotedStatus>({ connected: false, email: null });
  const [qwotedEmail, setQwotedEmail] = useState("");
  const [qwotedPassword, setQwotedPassword] = useState("");
  const [pitchEdits, setPitchEdits] = useState<Record<string, string>>({});
  const [relevanceMap, setRelevanceMap] = useState<Record<string, { is_relevant: boolean; confidence: number }>>({});

  // Admin create officer
  const [newOfficerName, setNewOfficerName] = useState("");
  const [newOfficerEmail, setNewOfficerEmail] = useState("");
  const [newOfficerPassword, setNewOfficerPassword] = useState("");

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    const saved = window.localStorage.getItem("pr_access_token");
    if (saved) setToken(saved);
  }, []);

  useEffect(() => {
    if (!token) return;
    let active = true;
    (async () => {
      try {
        const me = await apiFetch<UserMe>("/api/v1/auth/me", {}, token);
        const [cls, jrn, qw] = await Promise.all([
          apiFetch<ClientItem[]>("/api/v1/clients", {}, token),
          apiFetch<JournalistItem[]>("/api/v1/journalists", {}, token),
          apiFetch<QwotedStatus>("/api/v1/qwoted/status", {}, token).catch(() => ({
            connected: false,
            email: null,
          })),
        ]);
        if (!active) return;
        setUser(me);
        setClients(cls);
        setQwotedStatus(qw);
        if (cls.length > 0 && !selectedClientId) {
          setSelectedClientId(cls[0].id);
        }
        setJournalists(jrn);
        if (me.role === "admin") {
          const users = await apiFetch<UserMe[]>("/api/v1/users", {}, token);
          if (active) setOfficers(users);
        }
      } catch {
        if (active) {
          window.localStorage.removeItem("pr_access_token");
          setToken(null);
          setUser(null);
        }
      }
    })();
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  async function handleLogin(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const res = await apiFetch<{ access_token: string; refresh_token: string }>("/api/v1/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      window.localStorage.setItem("pr_access_token", res.access_token);
      window.localStorage.setItem("pr_refresh_token", res.refresh_token);
      setToken(res.access_token);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setBusy(false);
    }
  }

  async function handleCreateClient(e: React.FormEvent) {
    e.preventDefault();
    if (!token) return;
    setError(null);
    setNotice(null);
    try {
      const created = await apiFetch<ClientItem>(
        "/api/v1/clients",
        {
          method: "POST",
          body: JSON.stringify({
            name: clientName,
            domain: clientDomain,
            industry: clientIndustry,
            brand_voice: brandVoice,
            key_facts: {
              visa_category: visaCategory,
              role_title: roleTitle,
              company: companyName,
            },
          }),
        },
        token,
      );
      setClients((prev) => [created, ...prev]);
      if (!selectedClientId) setSelectedClientId(created.id);
      setClientName("");
      setRoleTitle("");
      setCompanyName("");
      setClientDomain("");
      setClientIndustry("");
      setBrandVoice("");
      setNotice(`Added candidate "${created.name}".`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create client");
    }
  }

  async function handleScrapeJournalist(e: React.FormEvent) {
    e.preventDefault();
    if (!token) return;
    setError(null);
    setNotice(null);
    setBusy(true);
    try {
      const created = await apiFetch<JournalistItem>(
        "/api/v1/journalists/scrape",
        {
          method: "POST",
          body: JSON.stringify({ url: scrapeUrl, beat: scrapeBeat }),
        },
        token,
      );
      setJournalists((prev) => [created, ...prev.filter((j) => j.id !== created.id)]);
      setScrapeUrl("");
      setScrapeBeat("");
      setNotice(`Scraped ${created.canonical_name} (${created.articles.length} articles).`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to scrape journalist");
    } finally {
      setBusy(false);
    }
  }

  async function handleVerifyEmail(journalistId: string) {
    if (!token) return;
    setError(null);
    try {
      const res = await apiFetch<{ email_status: string; detail: string }>(
        `/api/v1/journalists/${journalistId}/verify-email`,
        { method: "POST" },
        token,
      );
      setJournalists((prev) =>
        prev.map((j) => (j.id === journalistId ? { ...j, email_status: res.email_status } : j)),
      );
      setCuratedMatches((prev) =>
        prev.map((m) =>
          m.journalist_id === journalistId ? { ...m, email_status: res.email_status } : m,
        ),
      );
      setNotice(`Verification result: ${res.email_status} (${res.detail})`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to verify email");
    }
  }

  async function handleRefreshJournalist(journalistId: string) {
    if (!token) return;
    setError(null);
    try {
      const updated = await apiFetch<JournalistItem>(
        `/api/v1/journalists/${journalistId}/refresh`,
        { method: "POST" },
        token,
      );
      setJournalists((prev) => prev.map((j) => (j.id === journalistId ? updated : j)));
      setNotice(`Refreshed ${updated.canonical_name}.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to refresh journalist");
    }
  }

  async function handleScoreRelevance(journalistId: string) {
    if (!token || !selectedClientId) return;
    setError(null);
    try {
      const res = await apiFetch<{ is_relevant: boolean; confidence: number }>(
        `/api/v1/journalists/${journalistId}/score-relevance?client_id=${selectedClientId}`,
        { method: "POST" },
        token,
      );
      setRelevanceMap((prev) => ({ ...prev, [journalistId]: res }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to score relevance");
    }
  }

  async function handleCreateOfficer(e: React.FormEvent) {
    e.preventDefault();
    if (!token) return;
    setError(null);
    try {
      const created = await apiFetch<UserMe>(
        "/api/v1/users",
        {
          method: "POST",
          body: JSON.stringify({
            full_name: newOfficerName,
            email: newOfficerEmail,
            password: newOfficerPassword,
            role: "officer",
          }),
        },
        token,
      );
      setOfficers((prev) => [created, ...prev]);
      setNewOfficerName("");
      setNewOfficerEmail("");
      setNewOfficerPassword("");
      setNotice(`Created PR Officer "${created.full_name}".`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create officer");
    }
  }

  async function handleDiscoverForClient(client: ClientItem) {
    if (!token) return;
    setError(null);
    setNotice(`Discovering journalists in bulk for ${client.name}...`);
    setBusy(true);
    try {
      const matches = await apiFetch<CuratedJournalistMatch[]>(
        `/api/v1/clients/${client.id}/discover-journalists`,
        {
          method: "POST",
          body: JSON.stringify({ max_results: 10 }),
        },
        token,
      );
      setCuratedClient(client);
      setCuratedMatches(matches);
      setSelectedClientId(client.id);
      const jrn = await apiFetch<JournalistItem[]>("/api/v1/journalists", {}, token);
      setJournalists(jrn);
      setNotice(`Found ${matches.length} curated journalists for ${client.name}.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to discover journalists");
    } finally {
      setBusy(false);
    }
  }

  async function handleFindCuratedForClient(client: ClientItem, refreshScores = false) {
    if (!token) return;
    setError(null);
    setCuratedClient(client);
    setSelectedClientId(client.id);
    setBusy(true);
    try {
      const suffix = refreshScores ? "?refresh_scores=true" : "";
      const [matches, opps, pit, fus] = await Promise.all([
        apiFetch<CuratedJournalistMatch[]>(
          `/api/v1/clients/${client.id}/curated-journalists${suffix}`,
          {},
          token,
        ),
        apiFetch<PrOpportunityItem[]>(`/api/v1/clients/${client.id}/opportunities`, {}, token),
        apiFetch<PitchItem[]>(`/api/v1/clients/${client.id}/pitches`, {}, token),
        apiFetch<FollowUpItem[]>(`/api/v1/clients/${client.id}/follow-ups`, {}, token),
      ]);
      setCuratedMatches(matches);
      setOpportunities(opps);
      setPitches(pit);
      setFollowUps(fus);
      if (matches.length > 0 && !fuJournalistId) {
        setFuJournalistId(matches[0].journalist_id);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to curate journalists");
    } finally {
      setBusy(false);
    }
  }

  async function handleConnectQwoted(e: React.FormEvent) {
    e.preventDefault();
    if (!token) return;
    setError(null);
    setBusy(true);
    try {
      const res = await apiFetch<QwotedStatus>(
        "/api/v1/qwoted/connect",
        {
          method: "POST",
          body: JSON.stringify({ email: qwotedEmail, password: qwotedPassword }),
        },
        token,
      );
      setQwotedStatus(res);
      setQwotedPassword("");
      setNotice(`Connected Qwoted account (${res.email}).`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to connect Qwoted");
    } finally {
      setBusy(false);
    }
  }

  async function handleDisconnectQwoted() {
    if (!token) return;
    setError(null);
    try {
      const res = await apiFetch<QwotedStatus>("/api/v1/qwoted/disconnect", { method: "POST" }, token);
      setQwotedStatus(res);
      setNotice("Disconnected Qwoted account.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to disconnect Qwoted");
    }
  }

  async function handleDiscoverOpportunities(client: ClientItem) {
    if (!token) return;
    setError(null);
    setBusy(true);
    setNotice(`Discovering curated PR opportunities for ${client.name}...`);
    try {
      const opps = await apiFetch<PrOpportunityItem[]>(
        `/api/v1/clients/${client.id}/discover-opportunities`,
        {
          method: "POST",
          body: JSON.stringify({ max_results: 10 }),
        },
        token,
      );
      setOpportunities(opps);
      setWorkspaceSubTab("opportunities");
      setNotice(`Found ${opps.length} PR opportunities for ${client.name}.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to discover PR opportunities");
    } finally {
      setBusy(false);
    }
  }

  async function handleDraftPitch(opportunityId: string) {
    if (!token) return;
    setError(null);
    try {
      const updated = await apiFetch<PrOpportunityItem>(
        `/api/v1/opportunities/${opportunityId}/draft-pitch`,
        { method: "POST", body: JSON.stringify({}) },
        token,
      );
      setOpportunities((prev) => prev.map((o) => (o.id === opportunityId ? updated : o)));
      setPitchEdits((prev) => ({ ...prev, [opportunityId]: updated.draft_pitch }));
      setNotice("Drafted grounded pitch using candidate's approved achievements.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to draft pitch");
    }
  }

  async function handleSubmitPitch(opportunityId: string) {
    if (!token) return;
    setError(null);
    const bodyText = pitchEdits[opportunityId];
    try {
      const res = await apiFetch<{ pitch_status: string; mode: string; detail: string }>(
        `/api/v1/opportunities/${opportunityId}/submit-pitch`,
        {
          method: "POST",
          body: JSON.stringify({ pitch_text: bodyText, confirm_live_submit: false }),
        },
        token,
      );
      setOpportunities((prev) =>
        prev.map((o) => (o.id === opportunityId ? { ...o, pitch_status: res.pitch_status } : o)),
      );
      setNotice(`${res.mode}: ${res.detail}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to submit pitch");
    }
  }

  async function handleGenerateJournalistPitch(clientId: string, journalistId: string) {
    if (!token) return;
    setError(null);
    setBusy(true);
    try {
      const created = await apiFetch<PitchItem>(
        "/api/v1/pitches/generate",
        {
          method: "POST",
          body: JSON.stringify({ client_id: clientId, journalist_id: journalistId }),
        },
        token,
      );
      setPitches((prev) => [created, ...prev]);
      setPitchSubjectEdits((prev) => ({ ...prev, [created.id]: created.subject }));
      setPitchBodyEdits((prev) => ({ ...prev, [created.id]: created.body }));
      setWorkspaceSubTab("pitches");
      setNotice(`Generated grounded pitch for ${created.journalist_name}.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to generate pitch");
    } finally {
      setBusy(false);
    }
  }

  async function handleSavePitchEdits(pitchId: string) {
    if (!token) return;
    setError(null);
    try {
      const updated = await apiFetch<PitchItem>(
        `/api/v1/pitches/${pitchId}`,
        {
          method: "PATCH",
          body: JSON.stringify({
            subject: pitchSubjectEdits[pitchId],
            body: pitchBodyEdits[pitchId],
          }),
        },
        token,
      );
      setPitches((prev) => prev.map((p) => (p.id === pitchId ? updated : p)));
      if (curatedClient) {
        const fus = await apiFetch<FollowUpItem[]>(
          `/api/v1/clients/${curatedClient.id}/follow-ups`,
          {},
          token,
        );
        setFollowUps(fus);
      }
      setNotice("Saved pitch edits.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save pitch edits");
    }
  }

  async function handleApproveSendPitch(pitchId: string) {
    if (!token) return;
    setError(null);
    try {
      const updated = await apiFetch<PitchItem>(
        `/api/v1/pitches/${pitchId}/approve-send`,
        { method: "POST" },
        token,
      );
      setPitches((prev) => prev.map((p) => (p.id === pitchId ? updated : p)));
      if (curatedClient) {
        const fus = await apiFetch<FollowUpItem[]>(
          `/api/v1/clients/${curatedClient.id}/follow-ups`,
          {},
          token,
        );
        setFollowUps(fus);
      }
      setNotice(`Pitch approved & dispatched (${updated.status}).`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to approve/send pitch");
    }
  }

  async function handleSimulateReply(pitchId: string) {
    if (!token) return;
    const replyBody = (replyInputs[pitchId] || "").trim();
    if (!replyBody) return;
    setError(null);
    try {
      const res = await apiFetch<ReplyClassificationResult>(
        `/api/v1/pitches/${pitchId}/simulate-reply`,
        { method: "POST", body: JSON.stringify({ body: replyBody }) },
        token,
      );
      setPitches((prev) =>
        prev.map((p) => (p.id === pitchId ? { ...p, status: res.pitch_status } : p)),
      );
      setReplyInputs((prev) => ({ ...prev, [pitchId]: "" }));
      setNotice(res.detail);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to classify reply");
    }
  }

  async function handleCreateFollowUp(useAiTiming: boolean) {
    if (!token || !curatedClient || !fuJournalistId) return;
    setError(null);
    setBusy(true);
    try {
      const payload: Record<string, unknown> = {
        journalist_id: fuJournalistId,
        note: fuNote.trim(),
      };
      if (!useAiTiming && fuDueAt) {
        payload.due_at = new Date(fuDueAt).toISOString();
      }
      const created = await apiFetch<FollowUpItem>(
        `/api/v1/clients/${curatedClient.id}/follow-ups`,
        { method: "POST", body: JSON.stringify(payload) },
        token,
      );
      setFollowUps((prev) =>
        [...prev, created].sort((a, b) => a.due_at.localeCompare(b.due_at)),
      );
      setFuNote("");
      setFuDueAt("");
      setNotice(
        useAiTiming
          ? `AI scheduled follow-up for ${created.journalist_name} on ${new Date(created.due_at).toLocaleString()}.`
          : `Scheduled follow-up for ${created.journalist_name}.`,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to schedule follow-up");
    } finally {
      setBusy(false);
    }
  }

  async function handleUpdateFollowUp(
    followUpId: string,
    patch: { status?: "pending" | "completed" | "cancelled"; snooze_days?: number },
  ) {
    if (!token) return;
    setError(null);
    try {
      const updated = await apiFetch<FollowUpItem>(
        `/api/v1/follow-ups/${followUpId}`,
        { method: "PATCH", body: JSON.stringify(patch) },
        token,
      );
      setFollowUps((prev) =>
        prev
          .map((f) => (f.id === followUpId ? updated : f))
          .sort((a, b) => a.due_at.localeCompare(b.due_at)),
      );
      setNotice(
        patch.snooze_days
          ? `Snoozed follow-up by ${patch.snooze_days} days.`
          : `Updated follow-up status to ${updated.status}.`,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update follow-up");
    }
  }

  function handleLogout() {
    window.localStorage.removeItem("pr_access_token");
    window.localStorage.removeItem("pr_refresh_token");
    setToken(null);
    setUser(null);
    setClients([]);
    setJournalists([]);
    setOfficers([]);
  }

  const filteredJournalists = journalists.filter((j) => {
    if (!searchQuery.trim()) return true;
    const q = searchQuery.toLowerCase();
    return (
      j.canonical_name.toLowerCase().includes(q) ||
      j.outlet.toLowerCase().includes(q) ||
      j.beat.toLowerCase().includes(q)
    );
  });

  if (!user) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-gradient-to-br from-blue-50 via-white to-sky-50 px-4">
        <div className="w-full max-w-md rounded-2xl border border-blue-100 bg-white p-8 shadow-lg shadow-blue-900/5">
          <div className="flex items-center gap-2.5">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-blue-600 font-bold text-white">
              PR
            </div>
            <div>
              <h1 className="text-xl font-semibold text-slate-900">PR Automation</h1>
              <p className="text-xs text-slate-500">Multi-Tenant Outreach Platform</p>
            </div>
          </div>

          {error && (
            <div className="mt-5 rounded-lg border border-red-200 bg-red-50 px-3.5 py-2.5 text-sm text-red-700">
              {error}
            </div>
          )}

          <form onSubmit={handleLogin} className="mt-6 space-y-4">
            <div>
              <label className="block text-sm font-medium text-slate-700">Email</label>
              <input
                type="email"
                required
                placeholder="admin@example.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3.5 py-2 text-sm text-slate-900 outline-none transition focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-slate-700">Password</label>
              <input
                type="password"
                required
                placeholder="••••••••"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3.5 py-2 text-sm text-slate-900 outline-none transition focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
              />
            </div>
            <button
              type="submit"
              disabled={busy}
              className="w-full rounded-lg bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-blue-700 disabled:opacity-60"
            >
              {busy ? "Signing in..." : "Sign in"}
            </button>
          </form>
        </div>
      </main>
    );
  }

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4 px-6 py-4">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-blue-600 text-sm font-bold text-white">
              PR
            </div>
            <div>
              <h1 className="text-lg font-semibold text-slate-900">PR Automation</h1>
              <p className="text-xs text-slate-500">
                {user.full_name} · <span className="capitalize">{user.role}</span>
              </p>
            </div>
            <span className="ml-2 rounded-full border border-blue-200 bg-blue-50 px-2.5 py-0.5 text-xs font-medium text-blue-700">
              Dry-Run Sandbox
            </span>
          </div>

          <nav className="flex items-center gap-1 rounded-lg bg-slate-100 p-1">
            <button
              type="button"
              onClick={() => setTab("clients")}
              className={`rounded-md px-3.5 py-1.5 text-sm font-medium transition ${
                tab === "clients" ? "bg-white text-blue-600 shadow-sm" : "text-slate-600 hover:text-slate-900"
              }`}
            >
              Clients ({clients.length})
            </button>
            <button
              type="button"
              onClick={() => setTab("journalists")}
              className={`rounded-md px-3.5 py-1.5 text-sm font-medium transition ${
                tab === "journalists" ? "bg-white text-blue-600 shadow-sm" : "text-slate-600 hover:text-slate-900"
              }`}
            >
              Journalists ({journalists.length})
            </button>
            {user.role === "admin" && (
              <button
                type="button"
                onClick={() => setTab("team")}
                className={`rounded-md px-3.5 py-1.5 text-sm font-medium transition ${
                  tab === "team" ? "bg-white text-blue-600 shadow-sm" : "text-slate-600 hover:text-slate-900"
                }`}
              >
                Team ({officers.length})
              </button>
            )}
          </nav>

          <button
            onClick={handleLogout}
            className="rounded-lg border border-slate-300 bg-white px-3.5 py-1.5 text-sm font-medium text-slate-700 transition hover:bg-slate-50"
          >
            Sign out
          </button>
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-6 py-8">
        {error && (
          <div className="mb-6 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {error}
          </div>
        )}
        {notice && (
          <div className="mb-6 rounded-xl border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-800">
            {notice}
          </div>
        )}

        {tab === "clients" && curatedClient && (
          <section className="space-y-5">
            <div className="rounded-2xl border border-blue-200 bg-white p-6 shadow-sm">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div>
                  <button
                    type="button"
                    onClick={() => setCuratedClient(null)}
                    className="mb-3 inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-slate-50 px-3 py-1 text-xs font-medium text-slate-700 transition hover:bg-slate-100"
                  >
                    ← Back to All Candidates
                  </button>
                  <div className="flex flex-wrap items-center gap-2.5">
                    <h2 className="text-xl font-bold text-slate-900">{curatedClient.name}</h2>
                    {curatedClient.key_facts?.visa_category && (
                      <span className="rounded-full border border-blue-200 bg-blue-50 px-2.5 py-0.5 text-xs font-semibold text-blue-700">
                        {curatedClient.key_facts.visa_category}
                      </span>
                    )}
                    <span className="rounded-full bg-emerald-50 px-2.5 py-0.5 text-xs font-medium text-emerald-700">
                      Active
                    </span>
                  </div>
                  <p className="mt-1 text-sm text-slate-600">
                    {[curatedClient.key_facts?.role_title, curatedClient.key_facts?.company]
                      .filter(Boolean)
                      .join(" @ ")}
                    {(curatedClient.key_facts?.role_title || curatedClient.key_facts?.company) &&
                    curatedClient.industry
                      ? " · "
                      : ""}
                    {curatedClient.industry}
                    {curatedClient.domain ? ` · ${curatedClient.domain}` : ""}
                  </p>
                  {curatedClient.brand_voice && (
                    <p className="mt-2 max-w-4xl text-xs leading-relaxed text-slate-600">
                      {curatedClient.brand_voice}
                    </p>
                  )}
                </div>

                <div className="flex flex-col items-end gap-2">
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => handleDiscoverForClient(curatedClient)}
                    className="rounded-lg bg-blue-600 px-4 py-2 text-xs font-semibold text-white transition hover:bg-blue-700 disabled:opacity-60"
                  >
                    {busy ? "Discovering..." : "Auto-Discover Journalists"}
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => handleDiscoverOpportunities(curatedClient)}
                    className="rounded-lg border border-blue-600 bg-white px-4 py-1.5 text-xs font-semibold text-blue-700 transition hover:bg-blue-50 disabled:opacity-60"
                  >
                    Find PR Opportunities
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => handleFindCuratedForClient(curatedClient, true)}
                    className="rounded-lg border border-blue-200 bg-blue-50 px-4 py-1.5 text-xs font-medium text-blue-700 transition hover:bg-blue-100 disabled:opacity-60"
                  >
                    Refresh Laya Ranking
                  </button>
                </div>
              </div>
            </div>

            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setWorkspaceSubTab("journalists")}
                className={`rounded-lg px-4 py-2 text-xs font-semibold transition ${
                  workspaceSubTab === "journalists"
                    ? "bg-blue-600 text-white"
                    : "border border-slate-200 bg-white text-slate-700 hover:bg-slate-50"
                }`}
              >
                Curated Journalists ({curatedMatches.length})
              </button>
              <button
                type="button"
                onClick={() => setWorkspaceSubTab("opportunities")}
                className={`rounded-lg px-4 py-2 text-xs font-semibold transition ${
                  workspaceSubTab === "opportunities"
                    ? "bg-blue-600 text-white"
                    : "border border-slate-200 bg-white text-slate-700 hover:bg-slate-50"
                }`}
              >
                PR Opportunities &amp; Qwoted ({opportunities.length})
              </button>
              <button
                type="button"
                onClick={() => setWorkspaceSubTab("pitches")}
                className={`rounded-lg px-4 py-2 text-xs font-semibold transition ${
                  workspaceSubTab === "pitches"
                    ? "bg-blue-600 text-white"
                    : "border border-slate-200 bg-white text-slate-700 hover:bg-slate-50"
                }`}
              >
                Outreach &amp; Pitches ({pitches.length})
              </button>
              <button
                type="button"
                onClick={() => setWorkspaceSubTab("calendar")}
                className={`rounded-lg px-4 py-2 text-xs font-semibold transition ${
                  workspaceSubTab === "calendar"
                    ? "bg-blue-600 text-white"
                    : "border border-slate-200 bg-white text-slate-700 hover:bg-slate-50"
                }`}
              >
                Follow-Up Calendar ({followUps.filter((f) => f.status === "pending").length})
              </button>
            </div>

            {workspaceSubTab === "calendar" && (
              <div className="space-y-4">
                <div className="rounded-2xl border border-blue-100 bg-white p-6 shadow-sm">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div>
                      <h3 className="text-base font-semibold text-slate-900">
                        AI-Curated Follow-Up Calendar for {curatedClient.name}
                      </h3>
                      <p className="text-xs text-slate-500">
                        Follow-ups are auto-scheduled when you send a pitch using AI-curated mid-week morning editorial windows for maximum reply probability.
                      </p>
                    </div>
                  </div>

                  <div className="mt-4 grid gap-3 sm:grid-cols-4">
                    <select
                      value={fuJournalistId}
                      onChange={(e) => setFuJournalistId(e.target.value)}
                      className="rounded-lg border border-slate-300 px-3 py-2 text-xs text-slate-800"
                    >
                      <option value="">Select journalist...</option>
                      {curatedMatches.map((m) => (
                        <option key={m.journalist_id} value={m.journalist_id}>
                          {m.canonical_name} ({m.outlet})
                        </option>
                      ))}
                    </select>
                    <input
                      type="datetime-local"
                      value={fuDueAt}
                      onChange={(e) => setFuDueAt(e.target.value)}
                      className="rounded-lg border border-slate-300 px-3 py-2 text-xs text-slate-800"
                      title="Optional manual date/time (leave empty for AI Best Time)"
                    />
                    <input
                      type="text"
                      value={fuNote}
                      onChange={(e) => setFuNote(e.target.value)}
                      placeholder="Follow-up note (optional)"
                      className="rounded-lg border border-slate-300 px-3 py-2 text-xs text-slate-800"
                    />
                    <div className="flex gap-2">
                      <button
                        type="button"
                        disabled={busy || !fuJournalistId}
                        onClick={() => handleCreateFollowUp(true)}
                        className="flex-1 rounded-lg bg-blue-600 px-3 py-2 text-xs font-semibold text-white hover:bg-blue-700 disabled:opacity-50"
                      >
                        AI Best Time
                      </button>
                      {fuDueAt && (
                        <button
                          type="button"
                          disabled={busy || !fuJournalistId}
                          onClick={() => handleCreateFollowUp(false)}
                          className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                        >
                          Manual
                        </button>
                      )}
                    </div>
                  </div>
                </div>

                <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
                  <div className="space-y-3">
                    {followUps.map((fu) => (
                      <div
                        key={fu.id}
                        className={`rounded-xl border p-4 ${
                          fu.is_overdue
                            ? "border-red-300 bg-red-50/40"
                            : fu.status === "pending"
                              ? "border-blue-100 bg-slate-50/40"
                              : "border-slate-200 bg-slate-50 opacity-75"
                        }`}
                      >
                        <div className="flex flex-wrap items-start justify-between gap-3">
                          <div>
                            <div className="flex flex-wrap items-center gap-2">
                              <span className="text-sm font-semibold text-slate-900">
                                {fu.journalist_name}
                              </span>
                              <span className="text-xs text-slate-500">
                                {fu.journalist_outlet}
                              </span>
                              {fu.is_overdue ? (
                                <span className="rounded-full bg-red-100 px-2.5 py-0.5 text-[11px] font-semibold text-red-700">
                                  Overdue
                                </span>
                              ) : (
                                <span
                                  className={`rounded-full px-2.5 py-0.5 text-[11px] font-semibold ${
                                    fu.status === "pending"
                                      ? "bg-blue-100 text-blue-700"
                                      : fu.status === "completed"
                                        ? "bg-emerald-100 text-emerald-700"
                                        : "bg-slate-200 text-slate-600"
                                  }`}
                                >
                                  {fu.status}
                                </span>
                              )}
                            </div>
                            <p className="mt-1 text-xs font-medium text-slate-700">
                              Due: {new Date(fu.due_at).toLocaleString()}
                              {fu.journalist_email ? ` · ${fu.journalist_email}` : ""}
                            </p>
                            {fu.note && (
                              <p className="mt-1 text-xs text-slate-600">{fu.note}</p>
                            )}
                          </div>

                          {fu.status === "pending" && (
                            <div className="flex flex-wrap gap-2">
                              <button
                                type="button"
                                onClick={() =>
                                  handleUpdateFollowUp(fu.id, { status: "completed" })
                                }
                                className="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-emerald-700"
                              >
                                Mark Done
                              </button>
                              <button
                                type="button"
                                onClick={() =>
                                  handleUpdateFollowUp(fu.id, { snooze_days: 3 })
                                }
                                className="rounded-lg border border-blue-200 bg-white px-3 py-1.5 text-xs font-semibold text-blue-700 hover:bg-blue-50"
                              >
                                Snooze +3d
                              </button>
                              <button
                                type="button"
                                onClick={() =>
                                  handleUpdateFollowUp(fu.id, { status: "cancelled" })
                                }
                                className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-semibold text-slate-600 hover:bg-slate-50"
                              >
                                Cancel
                              </button>
                            </div>
                          )}
                        </div>
                      </div>
                    ))}

                    {followUps.length === 0 && (
                      <p className="py-8 text-center text-sm text-slate-500">
                        No follow-ups scheduled for {curatedClient.name} yet. Send a pitch or click &quot;AI Best Time&quot; above.
                      </p>
                    )}
                  </div>
                </div>
              </div>
            )}

            {workspaceSubTab === "pitches" && (
              <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <h3 className="text-base font-semibold text-slate-900">
                      Outreach Review Queue for {curatedClient.name} ({pitches.length})
                    </h3>
                    <p className="text-xs text-slate-500">
                      Human-in-the-loop approval required before dispatch. Mode: Dry Run (no live emails sent).
                    </p>
                  </div>
                </div>

                <div className="mt-4 space-y-4">
                  {pitches.map((p) => {
                    const subjVal = pitchSubjectEdits[p.id] ?? p.subject;
                    const bodyVal = pitchBodyEdits[p.id] ?? p.body;
                    return (
                      <div key={p.id} className="rounded-xl border border-slate-200 bg-slate-50/50 p-4">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <div>
                            <span className="text-sm font-semibold text-slate-900">
                              {p.journalist_name}
                            </span>{" "}
                            <span className="text-xs text-slate-500">
                              · {p.journalist_outlet} ·{" "}
                              <span className="font-mono text-blue-700">
                                {p.journalist_email || "no email"}
                              </span>
                            </span>
                          </div>
                          <div className="flex items-center gap-2 text-xs">
                            <span
                              className={`rounded-full border px-2 py-0.5 font-medium ${
                                STATUS_BADGE[p.journalist_email_status] ?? STATUS_BADGE.missing
                              }`}
                            >
                              {p.journalist_email_status}
                            </span>
                            <span className="rounded-full border border-blue-200 bg-blue-50 px-2.5 py-0.5 font-semibold text-blue-700">
                              {p.status}
                            </span>
                          </div>
                        </div>

                        {p.cited_article_title && (
                          <p className="mt-1 text-xs text-slate-500">
                            Citing:{" "}
                            <a
                              href={p.cited_article_url}
                              target="_blank"
                              rel="noreferrer"
                              className="text-blue-600 hover:underline"
                            >
                              {p.cited_article_title}
                            </a>
                          </p>
                        )}

                        <div className="mt-3 space-y-2">
                          <input
                            type="text"
                            disabled={p.status !== "drafted"}
                            value={subjVal}
                            onChange={(e) =>
                              setPitchSubjectEdits((prev) => ({ ...prev, [p.id]: e.target.value }))
                            }
                            className="w-full rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-900 outline-none focus:border-blue-500 disabled:bg-slate-100"
                          />
                          <textarea
                            rows={6}
                            disabled={p.status !== "drafted"}
                            value={bodyVal}
                            onChange={(e) =>
                              setPitchBodyEdits((prev) => ({ ...prev, [p.id]: e.target.value }))
                            }
                            className="w-full rounded-lg border border-slate-300 bg-white p-2.5 text-xs text-slate-800 outline-none focus:border-blue-500 disabled:bg-slate-100"
                          />
                        </div>

                        {p.status === "drafted" && (
                          <div className="mt-3 flex flex-wrap justify-end gap-2">
                            <button
                              type="button"
                              onClick={() => handleSavePitchEdits(p.id)}
                              className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
                            >
                              Save Edits
                            </button>
                            <button
                              type="button"
                              onClick={() => handleApproveSendPitch(p.id)}
                              className="rounded-lg bg-blue-600 px-3.5 py-1.5 text-xs font-semibold text-white hover:bg-blue-700"
                            >
                              Approve &amp; Send (Dry Run)
                            </button>
                          </div>
                        )}

                        {(p.status === "sent_dry_run" || p.status === "sent") && (
                          <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-slate-200 pt-3">
                            <input
                              type="text"
                              placeholder="Paste or simulate journalist reply (e.g. 'Happy to chat' or 'Unsubscribe')..."
                              value={replyInputs[p.id] ?? ""}
                              onChange={(e) =>
                                setReplyInputs((prev) => ({ ...prev, [p.id]: e.target.value }))
                              }
                              className="flex-1 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs outline-none focus:border-blue-500"
                            />
                            <button
                              type="button"
                              onClick={() => handleSimulateReply(p.id)}
                              className="rounded-lg border border-blue-200 bg-blue-50 px-3 py-1.5 text-xs font-medium text-blue-700 hover:bg-blue-100"
                            >
                              Classify Reply
                            </button>
                          </div>
                        )}
                      </div>
                    );
                  })}

                  {pitches.length === 0 && (
                    <p className="py-8 text-center text-sm text-slate-500">
                      No pitches drafted for {curatedClient.name} yet. Click &quot;Draft Pitch&quot; on any journalist in the Curated Journalists tab.
                    </p>
                  )}
                </div>
              </div>
            )}

            {workspaceSubTab === "opportunities" && (
              <div className="space-y-4">
                <div className="rounded-2xl border border-blue-100 bg-white p-5 shadow-sm">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                      <h3 className="text-sm font-semibold text-slate-900">
                        Qwoted Integration (HARO-style Source Requests)
                      </h3>
                      <p className="text-xs text-slate-500">
                        Connect your Qwoted account to search live reporter requests via Algolia and draft pitches.
                      </p>
                    </div>
                    {qwotedStatus.connected ? (
                      <div className="flex items-center gap-2">
                        <span className="rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1 text-xs font-medium text-emerald-700">
                          Connected: {qwotedStatus.email}
                        </span>
                        <button
                          type="button"
                          onClick={handleDisconnectQwoted}
                          className="rounded-lg border border-slate-200 px-3 py-1 text-xs text-slate-600 hover:bg-slate-50"
                        >
                          Disconnect
                        </button>
                      </div>
                    ) : (
                      <form onSubmit={handleConnectQwoted} className="flex flex-wrap items-center gap-2">
                        <input
                          type="email"
                          required
                          placeholder="Qwoted email"
                          value={qwotedEmail}
                          onChange={(e) => setQwotedEmail(e.target.value)}
                          className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs outline-none focus:border-blue-500"
                        />
                        <input
                          type="password"
                          required
                          placeholder="Qwoted password"
                          value={qwotedPassword}
                          onChange={(e) => setQwotedPassword(e.target.value)}
                          className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs outline-none focus:border-blue-500"
                        />
                        <button
                          type="submit"
                          disabled={busy}
                          className="rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-blue-700 disabled:opacity-60"
                        >
                          Connect Qwoted
                        </button>
                      </form>
                    )}
                  </div>
                </div>

                <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div>
                      <h3 className="text-base font-semibold text-slate-900">
                        Curated PR Opportunities for {curatedClient.name} ({opportunities.length})
                      </h3>
                      <p className="text-xs text-slate-500">
                        Live Qwoted requests &amp; public journalist callouts where {curatedClient.name} can pitch directly.
                      </p>
                    </div>
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => handleDiscoverOpportunities(curatedClient)}
                      className="rounded-lg bg-blue-600 px-3.5 py-1.5 text-xs font-semibold text-white hover:bg-blue-700 disabled:opacity-60"
                    >
                      {busy ? "Searching..." : "Discover Opportunities"}
                    </button>
                  </div>

                  <div className="mt-4 space-y-3">
                    {opportunities.map((opp) => {
                      const draftValue = pitchEdits[opp.id] ?? opp.draft_pitch;
                      return (
                        <div
                          key={opp.id}
                          className="rounded-xl border border-slate-200 bg-slate-50/50 p-4"
                        >
                          <div className="flex flex-wrap items-start justify-between gap-2">
                            <div>
                              <div className="flex flex-wrap items-center gap-2">
                                <span className="rounded-full border border-blue-200 bg-blue-50 px-2 py-0.5 text-[11px] font-semibold uppercase text-blue-700">
                                  {opp.source_platform === "qwoted" ? "Qwoted" : "Public Callout"}
                                </span>
                                <a
                                  href={opp.share_url}
                                  target="_blank"
                                  rel="noreferrer"
                                  className="text-sm font-semibold text-slate-900 hover:text-blue-700 hover:underline"
                                >
                                  {opp.title}
                                </a>
                              </div>
                              <p className="mt-1 text-xs text-slate-500">
                                {opp.publication}
                                {opp.journalist_name ? ` · ${opp.journalist_name}` : ""}
                                {opp.deadline ? ` · Deadline/Date: ${opp.deadline}` : ""}
                              </p>
                            </div>
                            <div className="flex items-center gap-1.5 text-xs">
                              <span
                                className={`rounded-full border px-2 py-0.5 font-medium ${
                                  opp.is_relevant
                                    ? "border-blue-200 bg-blue-100 text-blue-800"
                                    : "border-slate-200 bg-white text-slate-600"
                                }`}
                              >
                                {opp.is_relevant ? "Match" : "Low fit"} · {Math.round(opp.confidence * 100)}%
                              </span>
                              <span className="rounded-full border border-slate-200 bg-white px-2 py-0.5 font-medium text-slate-700">
                                {opp.pitch_status}
                              </span>
                            </div>
                          </div>

                          {opp.summary && (
                            <p className="mt-2 text-xs leading-relaxed text-slate-600">
                              {opp.summary}
                            </p>
                          )}

                          <div className="mt-3 flex flex-wrap items-center gap-2">
                            <button
                              type="button"
                              onClick={() => handleDraftPitch(opp.id)}
                              className="rounded-lg border border-blue-200 bg-blue-50 px-3 py-1 text-xs font-medium text-blue-700 hover:bg-blue-100"
                            >
                              Draft Pitch for {curatedClient.name}
                            </button>
                            <a
                              href={opp.share_url}
                              target="_blank"
                              rel="noreferrer"
                              className="rounded-lg border border-slate-200 bg-white px-3 py-1 text-xs font-medium text-slate-700 hover:bg-slate-100"
                            >
                              Open Request ↗
                            </a>
                          </div>

                          {(draftValue || opp.pitch_status === "drafted" || opp.pitch_status === "approved") && (
                            <div className="mt-3 space-y-2 border-t border-slate-200 pt-3">
                              <textarea
                                rows={5}
                                value={draftValue}
                                onChange={(e) =>
                                  setPitchEdits((prev) => ({ ...prev, [opp.id]: e.target.value }))
                                }
                                className="w-full rounded-lg border border-slate-300 bg-white p-2.5 text-xs text-slate-800 outline-none focus:border-blue-500"
                              />
                              <div className="flex justify-end">
                                <button
                                  type="button"
                                  onClick={() => handleSubmitPitch(opp.id)}
                                  className="rounded-lg bg-blue-600 px-3.5 py-1.5 text-xs font-semibold text-white hover:bg-blue-700"
                                >
                                  Approve &amp; Submit Pitch (Dry Run)
                                </button>
                              </div>
                            </div>
                          )}
                        </div>
                      );
                    })}

                    {opportunities.length === 0 && (
                      <div className="rounded-xl border border-dashed border-slate-300 py-10 text-center">
                        <p className="text-sm text-slate-600">
                          No PR opportunities discovered for {curatedClient.name} yet.
                        </p>
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => handleDiscoverOpportunities(curatedClient)}
                          className="mt-3 rounded-lg bg-blue-600 px-4 py-2 text-xs font-semibold text-white hover:bg-blue-700 disabled:opacity-60"
                        >
                          {busy ? "Searching..." : `Find PR Opportunities for ${curatedClient.name}`}
                        </button>
                      </div>
                    )}
                  </div>
                </div>
              </div>
            )}

            {workspaceSubTab === "journalists" && (
            <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <h3 className="text-base font-semibold text-slate-900">
                    Curated Journalists for {curatedClient.name} ({curatedMatches.length})
                  </h3>
                  <p className="text-xs text-slate-500">
                    Ranked specifically for {curatedClient.name}&apos;s field ({curatedClient.industry}) using Laya.
                  </p>
                </div>
                <span className="rounded-full border border-blue-200 bg-blue-50 px-3 py-1 text-xs font-medium text-blue-700">
                  {curatedMatches.filter((m) => Boolean(m.email)).length} with email
                </span>
              </div>

              <div className="mt-4 space-y-3">
                {curatedMatches.map((m) => (
                  <div
                    key={m.journalist_id}
                    className="rounded-xl border border-slate-200 bg-slate-50/50 p-4 transition hover:border-blue-200 hover:bg-blue-50/20"
                  >
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div>
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-sm font-semibold text-slate-900">
                            {m.canonical_name}
                          </span>
                          <span className="text-xs text-slate-500">
                            · {m.outlet} {m.beat ? `(${m.beat})` : ""}
                          </span>
                        </div>
                        <div className="mt-1.5 flex flex-wrap items-center gap-2 text-xs">
                          {m.email ? (
                            <span className="font-mono font-medium text-blue-700">{m.email}</span>
                          ) : (
                            <span className="text-slate-400">No public email found</span>
                          )}
                          <span
                            className={`rounded-full border px-2 py-0.5 font-medium ${
                              STATUS_BADGE[m.email_status] ?? STATUS_BADGE.missing
                            }`}
                          >
                            {m.email_status}
                          </span>
                          {m.email && (
                            <button
                              type="button"
                              onClick={() => handleVerifyEmail(m.journalist_id)}
                              className="rounded-md border border-blue-200 bg-white px-2.5 py-0.5 text-xs font-medium text-blue-700 transition hover:bg-blue-50"
                            >
                              Verify Email
                            </button>
                          )}
                          <button
                            type="button"
                            disabled={busy}
                            onClick={() =>
                              handleGenerateJournalistPitch(curatedClient.id, m.journalist_id)
                            }
                            className="rounded-md bg-blue-600 px-2.5 py-0.5 text-xs font-medium text-white transition hover:bg-blue-700 disabled:opacity-60"
                          >
                            Draft Pitch
                          </button>
                        </div>
                      </div>

                      <div className="flex items-center gap-2 text-xs">
                        <span
                          className={`rounded-full border px-2.5 py-0.5 font-medium ${
                            m.is_relevant
                              ? "border-blue-200 bg-blue-100 text-blue-800"
                              : "border-slate-200 bg-white text-slate-600"
                          }`}
                        >
                          {m.is_relevant ? "Match" : "Low fit"} · {Math.round(m.confidence * 100)}%
                        </span>
                        {m.in_cooldown && (
                          <span className="rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 font-medium text-amber-700">
                            Cooldown
                          </span>
                        )}
                        {m.suppressed && (
                          <span className="rounded-full border border-red-200 bg-red-50 px-2 py-0.5 font-medium text-red-700">
                            Suppressed
                          </span>
                        )}
                      </div>
                    </div>

                    {m.matched_article_title && (
                      <p className="mt-2 text-xs text-slate-600">
                        Recent hook:{" "}
                        {m.matched_article_url ? (
                          <a
                            href={m.matched_article_url}
                            target="_blank"
                            rel="noreferrer"
                            className="text-blue-600 hover:underline"
                          >
                            {m.matched_article_title}
                          </a>
                        ) : (
                          m.matched_article_title
                        )}
                      </p>
                    )}
                  </div>
                ))}

                {curatedMatches.length === 0 && (
                  <div className="rounded-xl border border-dashed border-slate-300 py-10 text-center">
                    <p className="text-sm text-slate-600">
                      No journalists discovered for {curatedClient.name} yet.
                    </p>
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => handleDiscoverForClient(curatedClient)}
                      className="mt-3 rounded-lg bg-blue-600 px-4 py-2 text-xs font-semibold text-white transition hover:bg-blue-700 disabled:opacity-60"
                    >
                      {busy ? "Discovering..." : `Auto-Discover Journalists for ${curatedClient.name}`}
                    </button>
                  </div>
                )}
              </div>
            </div>
            )}
          </section>
        )}

        {tab === "clients" && !curatedClient && (
          <section className="grid gap-6 md:grid-cols-5">
            <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm md:col-span-2">
              <h2 className="text-base font-semibold text-slate-900">Add Candidate Client</h2>
              <p className="mt-1 text-xs text-slate-500">
                Individual EB-1A / O-1A / EB-2 NIW candidates isolated to your officer account.
              </p>
              <form onSubmit={handleCreateClient} className="mt-4 space-y-3">
                <input
                  type="text"
                  placeholder="Candidate full name (e.g. Dr. Priya Nair)"
                  required
                  value={clientName}
                  onChange={(e) => setClientName(e.target.value)}
                  className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
                />
                <div className="grid grid-cols-2 gap-2">
                  <select
                    value={visaCategory}
                    onChange={(e) => setVisaCategory(e.target.value)}
                    className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-700 outline-none focus:border-blue-500"
                  >
                    <option value="EB-1A">EB-1A</option>
                    <option value="O-1A">O-1A</option>
                    <option value="EB-2 NIW">EB-2 NIW</option>
                    <option value="Other">Other</option>
                  </select>
                  <input
                    type="text"
                    placeholder="Field (e.g. AI Systems)"
                    value={clientIndustry}
                    onChange={(e) => setClientIndustry(e.target.value)}
                    className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
                  />
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <input
                    type="text"
                    placeholder="Role (e.g. Staff ML Eng)"
                    value={roleTitle}
                    onChange={(e) => setRoleTitle(e.target.value)}
                    className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
                  />
                  <input
                    type="text"
                    placeholder="Company (e.g. Stripe)"
                    value={companyName}
                    onChange={(e) => setCompanyName(e.target.value)}
                    className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
                  />
                </div>
                <input
                  type="text"
                  placeholder="LinkedIn / personal site / company URL (optional)"
                  value={clientDomain}
                  onChange={(e) => setClientDomain(e.target.value)}
                  className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
                />
                <textarea
                  placeholder="Approved achievements & press angle (awards, patents, scale metrics)..."
                  rows={3}
                  value={brandVoice}
                  onChange={(e) => setBrandVoice(e.target.value)}
                  className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
                />
                <button
                  type="submit"
                  className="w-full rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-blue-700"
                >
                  Create Candidate Client
                </button>
              </form>
            </div>

            <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm md:col-span-3">
              <h2 className="text-base font-semibold text-slate-900">Your Candidate Clients</h2>
              <div className="mt-4 space-y-3">
                {clients.map((c) => {
                  const visa = c.key_facts?.visa_category;
                  const role = c.key_facts?.role_title;
                  const company = c.key_facts?.company;
                  return (
                    <div
                      key={c.id}
                      onClick={() => handleFindCuratedForClient(c)}
                      className="flex cursor-pointer items-start justify-between rounded-xl border border-slate-200 bg-slate-50/60 p-4 transition hover:border-blue-400 hover:bg-blue-50/40"
                    >
                      <div>
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="font-semibold text-blue-700 hover:underline">{c.name}</span>
                          {visa && (
                            <span className="rounded-full border border-blue-200 bg-blue-50 px-2 py-0.5 text-xs font-medium text-blue-700">
                              {visa}
                            </span>
                          )}
                        </div>
                        <div className="mt-0.5 text-xs text-slate-500">
                          {[role, company].filter(Boolean).join(" @ ")}
                          {(role || company) && c.industry ? " · " : ""}
                          {c.industry}
                          {c.domain ? ` · ${c.domain}` : ""}
                        </div>
                        {c.brand_voice && (
                          <p className="mt-2 text-xs text-slate-600">{c.brand_voice}</p>
                        )}
                      </div>
                      <div className="flex shrink-0 flex-col items-end gap-2">
                        <span className="rounded-full bg-emerald-50 px-2.5 py-0.5 text-xs font-medium text-emerald-700">
                          Active
                        </span>
                        <button
                          type="button"
                          disabled={busy}
                          onClick={(e) => {
                            e.stopPropagation();
                            handleDiscoverForClient(c);
                          }}
                          className="rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-medium text-white transition hover:bg-blue-700 disabled:opacity-60"
                        >
                          {busy && selectedClientId === c.id ? "Discovering..." : "Auto-Discover Journalists"}
                        </button>
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            handleFindCuratedForClient(c);
                          }}
                          className="rounded-lg border border-blue-200 bg-blue-50 px-3 py-1 text-xs font-medium text-blue-700 transition hover:bg-blue-100"
                        >
                          Open Workspace →
                        </button>
                      </div>
                    </div>
                  );
                })}
                {clients.length === 0 && (
                  <p className="py-8 text-center text-sm text-slate-500">
                    No candidates yet. Add your first EB-1A / O-1A / EB-2 NIW client on the left.
                  </p>
                )}
              </div>
            </div>
          </section>
        )}

        {tab === "journalists" && (
          <section className="space-y-6">
            <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
              <h2 className="text-base font-semibold text-slate-900">Discover & Scrape Journalist</h2>
              <p className="mt-1 text-xs text-slate-500">
                Respects robots.txt and per-domain rate limits. Collects publicly listed contact info and recent articles.
              </p>
              <form onSubmit={handleScrapeJournalist} className="mt-4 flex flex-wrap gap-3">
                <input
                  type="url"
                  placeholder="Public author profile URL (https://...)"
                  required
                  value={scrapeUrl}
                  onChange={(e) => setScrapeUrl(e.target.value)}
                  className="min-w-64 flex-1 rounded-lg border border-slate-300 px-3.5 py-2 text-sm outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
                />
                <input
                  type="text"
                  placeholder="Beat (optional, e.g. AI)"
                  value={scrapeBeat}
                  onChange={(e) => setScrapeBeat(e.target.value)}
                  className="w-52 rounded-lg border border-slate-300 px-3.5 py-2 text-sm outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
                />
                <button
                  type="submit"
                  disabled={busy}
                  className="rounded-lg bg-blue-600 px-5 py-2 text-sm font-semibold text-white transition hover:bg-blue-700 disabled:opacity-60"
                >
                  {busy ? "Scraping..." : "Scrape Profile"}
                </button>
              </form>
            </div>

            <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <h2 className="text-base font-semibold text-slate-900">
                  Journalist Directory ({filteredJournalists.length})
                </h2>
                <div className="flex flex-wrap items-center gap-3">
                  {clients.length > 0 && (
                    <select
                      value={selectedClientId}
                      onChange={(e) => setSelectedClientId(e.target.value)}
                      className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs text-slate-700 outline-none focus:border-blue-500"
                    >
                      {clients.map((c) => (
                        <option key={c.id} value={c.id}>
                          Match against: {c.name}
                        </option>
                      ))}
                    </select>
                  )}
                  <input
                    type="search"
                    placeholder="Filter by name, outlet, beat..."
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    className="w-60 rounded-lg border border-slate-300 px-3 py-1.5 text-xs outline-none focus:border-blue-500"
                  />
                </div>
              </div>

              <div className="mt-5 space-y-4">
                {filteredJournalists.map((j) => {
                  const badge = STATUS_BADGE[j.email_status] ?? STATUS_BADGE.missing;
                  const rel = relevanceMap[j.id];
                  return (
                    <div key={j.id} className="rounded-xl border border-slate-200 bg-slate-50/50 p-4">
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div>
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="font-semibold text-slate-900">{j.canonical_name}</span>
                            <span className="text-xs text-slate-500">
                              {j.outlet} {j.beat ? `· ${j.beat}` : ""}
                            </span>
                            <span className={`rounded-full border px-2.5 py-0.5 text-xs font-medium ${badge}`}>
                              {j.email ?? "no email"} · {j.email_status}
                            </span>
                            {rel && (
                              <span
                                className={`rounded-full border px-2.5 py-0.5 text-xs font-medium ${
                                  rel.is_relevant
                                    ? "border-blue-200 bg-blue-50 text-blue-700"
                                    : "border-slate-200 bg-slate-100 text-slate-600"
                                }`}
                              >
                                {rel.is_relevant ? "Relevant" : "Low fit"} ({Math.round(rel.confidence * 100)}%)
                              </span>
                            )}
                          </div>
                          {j.profile_source_url && (
                            <p className="mt-1 text-xs text-slate-400">
                              Source:{" "}
                              <a
                                href={j.profile_source_url}
                                target="_blank"
                                rel="noreferrer"
                                className="text-blue-600 hover:underline"
                              >
                                {j.profile_source_url}
                              </a>
                            </p>
                          )}
                        </div>

                        <div className="flex flex-wrap items-center gap-2">
                          {j.email && (
                            <button
                              type="button"
                              onClick={() => handleVerifyEmail(j.id)}
                              className="rounded-lg border border-blue-200 bg-blue-50 px-3 py-1 text-xs font-medium text-blue-700 hover:bg-blue-100"
                            >
                              Verify Email
                            </button>
                          )}
                          <button
                            type="button"
                            onClick={() => handleRefreshJournalist(j.id)}
                            className="rounded-lg border border-slate-300 bg-white px-3 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50"
                          >
                            Refresh
                          </button>
                          {selectedClientId && (
                            <button
                              type="button"
                              onClick={() => handleScoreRelevance(j.id)}
                              className="rounded-lg border border-slate-300 bg-white px-3 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50"
                            >
                              Score Fit (Laya)
                            </button>
                          )}
                        </div>
                      </div>

                      {j.articles.length > 0 && (
                        <div className="mt-3 border-t border-slate-200 pt-3">
                          <div className="text-xs font-medium text-slate-500">Recent Articles</div>
                          <ul className="mt-1.5 space-y-1 text-xs">
                            {j.articles.slice(0, 5).map((a) => (
                              <li key={a.id}>
                                <a
                                  href={a.url}
                                  target="_blank"
                                  rel="noreferrer"
                                  className="text-blue-600 hover:underline"
                                >
                                  {a.title}
                                </a>
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}
                    </div>
                  );
                })}
                {filteredJournalists.length === 0 && (
                  <p className="py-8 text-center text-sm text-slate-500">
                    No journalists match your filter yet.
                  </p>
                )}
              </div>
            </div>
          </section>
        )}

        {tab === "team" && user.role === "admin" && (
          <section className="grid gap-6 md:grid-cols-5">
            <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm md:col-span-2">
              <h2 className="text-base font-semibold text-slate-900">Add PR Officer</h2>
              <form onSubmit={handleCreateOfficer} className="mt-4 space-y-3">
                <input
                  type="text"
                  placeholder="Full name"
                  required
                  value={newOfficerName}
                  onChange={(e) => setNewOfficerName(e.target.value)}
                  className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
                />
                <input
                  type="email"
                  placeholder="Email address"
                  required
                  value={newOfficerEmail}
                  onChange={(e) => setNewOfficerEmail(e.target.value)}
                  className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
                />
                <input
                  type="password"
                  placeholder="Temporary password (min 8 chars)"
                  required
                  minLength={8}
                  value={newOfficerPassword}
                  onChange={(e) => setNewOfficerPassword(e.target.value)}
                  className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
                />
                <button
                  type="submit"
                  className="w-full rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-blue-700"
                >
                  Create Officer
                </button>
              </form>
            </div>

            <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm md:col-span-3">
              <h2 className="text-base font-semibold text-slate-900">All Users ({officers.length})</h2>
              <div className="mt-4 grid gap-3 sm:grid-cols-2">
                {officers.map((o) => (
                  <div key={o.id} className="rounded-xl border border-slate-200 bg-slate-50/60 p-4">
                    <div className="font-semibold text-slate-900">{o.full_name}</div>
                    <div className="text-xs text-slate-500">{o.email}</div>
                    <div className="mt-2 flex items-center gap-2 text-xs">
                      <span className="rounded-full bg-blue-50 px-2 py-0.5 font-medium text-blue-700">
                        {o.role}
                      </span>
                      <span className="text-slate-500">{o.is_active ? "Active" : "Inactive"}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </section>
        )}
      </main>
    </div>
  );
}
