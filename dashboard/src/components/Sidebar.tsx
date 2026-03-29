"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Users, MessageCircle, Settings, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { useStore } from "@/lib/store";

const NAV_ITEMS = [
  { href: "/personas", icon: Users, label: "Assistants" },
  { href: "/chat", icon: MessageCircle, label: "Chat" },
  { href: "/settings", icon: Settings, label: "Settings" },
];

export function Sidebar() {
  const pathname = usePathname();
  const { sidebarOpen, toggleSidebar } = useStore();

  return (
    <>
      {/* Mobile overlay */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 bg-black/20 z-40 lg:hidden"
          onClick={toggleSidebar}
        />
      )}

      <aside
        className={cn(
          "fixed top-0 left-0 z-50 h-full w-64 bg-bg-secondary border-r border-border flex flex-col transition-transform duration-300",
          "lg:translate-x-0",
          sidebarOpen ? "translate-x-0" : "-translate-x-full"
        )}
      >
        {/* Logo */}
        <div className="p-5 flex items-center justify-between border-b border-border">
          <Link href="/personas" className="flex items-center gap-3 group">
            <span className="text-lg font-bold gradient-text">SuzyCloud</span>
          </Link>
          <button
            onClick={toggleSidebar}
            className="lg:hidden p-1 rounded hover:bg-bg-hover text-text-muted"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Status */}
        <div className="px-5 py-3 border-b border-border">
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 rounded-full bg-success" />
            <span className="text-xs text-text-muted">Multi-Tenant Platform</span>
          </div>
        </div>

        {/* Navigation */}
        <nav className="flex-1 overflow-y-auto py-3 px-3">
          {NAV_ITEMS.map((item) => {
            const isActive =
              pathname === item.href ||
              (item.href !== "/" && pathname.startsWith(item.href));
            return (
              <Link
                key={item.href}
                href={item.href}
                onClick={() => sidebarOpen && toggleSidebar()}
                className={cn(
                  "flex items-center gap-3 px-3 py-2.5 rounded-lg mb-0.5 text-sm transition-all",
                  isActive
                    ? "bg-brand/8 text-brand font-medium"
                    : "text-text-secondary hover:bg-bg-hover hover:text-text-primary"
                )}
              >
                <item.icon className="w-4.5 h-4.5 shrink-0" />
                {item.label}
              </Link>
            );
          })}
        </nav>

        {/* Bottom */}
        <div className="p-4 border-t border-border">
          <p className="text-[10px] text-text-muted">SuzyCloud v1.0</p>
        </div>
      </aside>
    </>
  );
}
