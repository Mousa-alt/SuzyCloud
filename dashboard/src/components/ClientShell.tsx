"use client";

import { type ReactNode, useEffect, useCallback } from "react";
import { Sidebar } from "./Sidebar";
import { useStore } from "@/lib/store";
import { apiFetch } from "@/lib/utils";

export function ClientShell({ children }: { children: ReactNode }) {
  const setPersonas = useStore((s) => s.setPersonas);

  const poll = useCallback(async () => {
    try {
      const data = await apiFetch<any[]>("/personas");
      if (data) setPersonas(data);
    } catch {}
  }, [setPersonas]);

  useEffect(() => {
    poll();
    const id = setInterval(poll, 10000);
    return () => clearInterval(id);
  }, [poll]);

  return (
    <div className="min-h-screen bg-bg-primary">
      <Sidebar />
      <main className="lg:ml-64 min-h-screen">{children}</main>
    </div>
  );
}
