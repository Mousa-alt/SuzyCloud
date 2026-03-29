"use client";

import { useEffect, useState, useCallback } from "react";
import { Header } from "@/components/Header";
import { Card } from "@/components/Card";
import { useStore, type Persona } from "@/lib/store";
import { apiFetch } from "@/lib/utils";
import { Plus, Trash2, Mail, Calendar, Bot, X } from "lucide-react";

const CLAUDE_MODELS = [
  { value: "claude-sonnet-4-6", label: "Sonnet" },
  { value: "claude-opus-4-6", label: "Opus" },
];

interface FormData {
  name: string;
  emoji: string;
  group_id: string;
  user_name: string;
  user_email: string;
  imap_host: string;
  imap_username: string;
  imap_password: string;
  claude_model: string;
}

const EMPTY_FORM: FormData = {
  name: "",
  emoji: "",
  group_id: "",
  user_name: "",
  user_email: "",
  imap_host: "",
  imap_username: "",
  imap_password: "",
  claude_model: "claude-sonnet-4-6",
};

export default function PersonasPage() {
  const { personas, setPersonas, removePersona } = useStore();
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<FormData>(EMPTY_FORM);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const loadPersonas = useCallback(async () => {
    try {
      const data = await apiFetch<{ personas: Persona[] }>("/personas");
      setPersonas(data.personas ?? []);
    } catch {
      // API may not be running yet
    }
  }, [setPersonas]);

  useEffect(() => {
    loadPersonas();
  }, [loadPersonas]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setSubmitting(true);

    try {
      await apiFetch("/personas", {
        method: "POST",
        body: JSON.stringify(form),
      });
      setForm(EMPTY_FORM);
      setShowForm(false);
      await loadPersonas();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create persona");
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (key: string) => {
    try {
      await apiFetch(`/personas/${key}`, { method: "DELETE" });
      removePersona(key);
    } catch {
      // Optimistic removal already done
    }
  };

  const updateField = (field: keyof FormData, value: string) => {
    setForm((prev) => ({ ...prev, [field]: value }));
  };

  return (
    <>
      <Header title="Assistants" />
      <div className="p-4 lg:p-6 space-y-6">
        {/* Action bar */}
        <div className="flex items-center justify-between">
          <p className="text-sm text-text-secondary">
            {personas.length} assistant{personas.length !== 1 ? "s" : ""}{" "}
            configured
          </p>
          <button
            onClick={() => setShowForm(!showForm)}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-brand text-white text-sm font-medium hover:bg-accent-hover transition-colors"
          >
            {showForm ? (
              <>
                <X className="w-4 h-4" /> Cancel
              </>
            ) : (
              <>
                <Plus className="w-4 h-4" /> New Assistant
              </>
            )}
          </button>
        </div>

        {/* New persona form */}
        {showForm && (
          <Card className="animate-in">
            <h3 className="text-sm font-semibold text-text-primary mb-4">
              Create New Assistant
            </h3>
            {error && (
              <div className="mb-4 px-3 py-2 rounded-lg bg-danger/8 text-danger text-sm border border-danger/20">
                {error}
              </div>
            )}
            <form onSubmit={handleSubmit} className="space-y-4">
              {/* Identity row */}
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                <div>
                  <label className="block text-xs text-text-muted mb-1">
                    Name *
                  </label>
                  <input
                    type="text"
                    required
                    value={form.name}
                    onChange={(e) => updateField("name", e.target.value)}
                    placeholder="Suzy"
                    className="w-full px-3 py-2 rounded-lg bg-bg-input border border-border text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-border-focus"
                  />
                </div>
                <div>
                  <label className="block text-xs text-text-muted mb-1">
                    Emoji
                  </label>
                  <input
                    type="text"
                    value={form.emoji}
                    onChange={(e) => updateField("emoji", e.target.value)}
                    placeholder="&#10024;"
                    className="w-full px-3 py-2 rounded-lg bg-bg-input border border-border text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-border-focus"
                  />
                </div>
                <div>
                  <label className="block text-xs text-text-muted mb-1">
                    Group ID *
                  </label>
                  <input
                    type="text"
                    required
                    value={form.group_id}
                    onChange={(e) => updateField("group_id", e.target.value)}
                    placeholder="120363XXX@g.us"
                    className="w-full px-3 py-2 rounded-lg bg-bg-input border border-border text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-border-focus"
                  />
                </div>
              </div>

              {/* User info */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs text-text-muted mb-1">
                    User Name *
                  </label>
                  <input
                    type="text"
                    required
                    value={form.user_name}
                    onChange={(e) => updateField("user_name", e.target.value)}
                    placeholder="Omar"
                    className="w-full px-3 py-2 rounded-lg bg-bg-input border border-border text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-border-focus"
                  />
                </div>
                <div>
                  <label className="block text-xs text-text-muted mb-1">
                    User Email
                  </label>
                  <input
                    type="email"
                    value={form.user_email}
                    onChange={(e) => updateField("user_email", e.target.value)}
                    placeholder="user@example.com"
                    className="w-full px-3 py-2 rounded-lg bg-bg-input border border-border text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-border-focus"
                  />
                </div>
              </div>

              {/* IMAP */}
              <div>
                <p className="text-xs font-medium text-text-secondary mb-2">
                  Email (IMAP) &mdash; optional
                </p>
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                  <div>
                    <label className="block text-xs text-text-muted mb-1">
                      IMAP Host
                    </label>
                    <input
                      type="text"
                      value={form.imap_host}
                      onChange={(e) => updateField("imap_host", e.target.value)}
                      placeholder="mail.example.com"
                      className="w-full px-3 py-2 rounded-lg bg-bg-input border border-border text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-border-focus"
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-text-muted mb-1">
                      IMAP Username
                    </label>
                    <input
                      type="text"
                      value={form.imap_username}
                      onChange={(e) =>
                        updateField("imap_username", e.target.value)
                      }
                      placeholder="user@example.com"
                      className="w-full px-3 py-2 rounded-lg bg-bg-input border border-border text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-border-focus"
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-text-muted mb-1">
                      IMAP Password
                    </label>
                    <input
                      type="password"
                      value={form.imap_password}
                      onChange={(e) =>
                        updateField("imap_password", e.target.value)
                      }
                      placeholder="password"
                      className="w-full px-3 py-2 rounded-lg bg-bg-input border border-border text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-border-focus"
                    />
                  </div>
                </div>
              </div>

              {/* Claude model */}
              <div className="max-w-xs">
                <label className="block text-xs text-text-muted mb-1">
                  Claude Model
                </label>
                <select
                  value={form.claude_model}
                  onChange={(e) => updateField("claude_model", e.target.value)}
                  className="w-full px-3 py-2 rounded-lg bg-bg-input border border-border text-sm text-text-primary focus:outline-none focus:border-border-focus"
                >
                  {CLAUDE_MODELS.map((m) => (
                    <option key={m.value} value={m.value}>
                      {m.label}
                    </option>
                  ))}
                </select>
              </div>

              {/* Submit */}
              <div className="flex justify-end pt-2">
                <button
                  type="submit"
                  disabled={submitting}
                  className="px-6 py-2 rounded-lg bg-brand text-white text-sm font-medium hover:bg-accent-hover transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {submitting ? "Creating..." : "Create Assistant"}
                </button>
              </div>
            </form>
          </Card>
        )}

        {/* Persona cards grid */}
        {personas.length === 0 ? (
          <Card>
            <div className="py-12 text-center">
              <Bot className="w-12 h-12 text-text-muted mx-auto mb-3" />
              <p className="text-sm text-text-muted">
                No assistants yet. Click &quot;New Assistant&quot; to create one.
              </p>
            </div>
          </Card>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {personas.map((p) => (
              <Card key={p.key} hover className="animate-in relative group">
                {/* Delete button */}
                <button
                  onClick={() => handleDelete(p.key)}
                  className="absolute top-3 right-3 p-1.5 rounded-lg text-text-muted hover:text-danger hover:bg-danger/8 opacity-0 group-hover:opacity-100 transition-all"
                  title="Delete assistant"
                >
                  <Trash2 className="w-4 h-4" />
                </button>

                {/* Persona info */}
                <div className="flex items-start gap-3 mb-3">
                  <span className="text-2xl">{p.emoji || "\u2728"}</span>
                  <div className="min-w-0">
                    <h3 className="text-sm font-semibold text-text-primary truncate">
                      {p.name}
                    </h3>
                    <p className="text-xs text-text-muted truncate">
                      {p.user_name}
                    </p>
                  </div>
                </div>

                {/* Model badge */}
                <div className="mb-3">
                  <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium bg-brand/8 text-brand border border-brand/20">
                    {p.chat_model.includes("opus") ? "Opus" : "Sonnet"}
                  </span>
                </div>

                {/* Group IDs */}
                <div className="mb-3">
                  <p className="text-[10px] text-text-muted mb-1">Groups</p>
                  {p.group_ids.map((gid) => (
                    <p
                      key={gid}
                      className="text-xs text-text-secondary font-mono truncate"
                    >
                      {gid}
                    </p>
                  ))}
                </div>

                {/* Feature flags */}
                <div className="flex items-center gap-2 pt-3 border-t border-border">
                  {p.email_enabled && (
                    <div
                      className="flex items-center gap-1 text-[10px] text-success"
                      title="Email enabled"
                    >
                      <Mail className="w-3 h-3" />
                      <span>Email</span>
                    </div>
                  )}
                  {p.calendar_enabled && (
                    <div
                      className="flex items-center gap-1 text-[10px] text-info"
                      title="Calendar enabled"
                    >
                      <Calendar className="w-3 h-3" />
                      <span>Calendar</span>
                    </div>
                  )}
                  {!p.email_enabled && !p.calendar_enabled && (
                    <p className="text-[10px] text-text-muted">
                      No integrations
                    </p>
                  )}
                </div>
              </Card>
            ))}
          </div>
        )}
      </div>
    </>
  );
}
