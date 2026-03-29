import { create } from "zustand";

export interface Persona {
  key: string;
  name: string;
  emoji: string;
  group_ids: string[];
  user_name: string;
  user_email: string;
  chat_model: string;
  email_enabled: boolean;
  calendar_enabled: boolean;
}

interface DashboardStore {
  personas: Persona[];
  sidebarOpen: boolean;
  loading: boolean;

  setPersonas: (personas: Persona[]) => void;
  addPersona: (persona: Persona) => void;
  removePersona: (key: string) => void;
  toggleSidebar: () => void;
  setLoading: (loading: boolean) => void;
}

export const useStore = create<DashboardStore>((set) => ({
  personas: [],
  sidebarOpen: false,
  loading: false,

  setPersonas: (personas) => set({ personas }),
  addPersona: (persona) =>
    set((s) => ({ personas: [...s.personas, persona] })),
  removePersona: (key) =>
    set((s) => ({ personas: s.personas.filter((p) => p.key !== key) })),
  toggleSidebar: () => set((s) => ({ sidebarOpen: !s.sidebarOpen })),
  setLoading: (loading) => set({ loading }),
}));
