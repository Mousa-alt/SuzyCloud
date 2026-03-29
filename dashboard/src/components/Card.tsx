"use client";

import { cn } from "@/lib/utils";
import { type ReactNode } from "react";

interface CardProps {
  children: ReactNode;
  className?: string;
  hover?: boolean;
}

export function Card({ children, className, hover }: CardProps) {
  return (
    <div
      className={cn(
        "bg-bg-card rounded-xl border border-border p-5",
        hover &&
          "hover:border-brand-lighter hover:shadow-sm transition-all cursor-pointer",
        className
      )}
    >
      {children}
    </div>
  );
}
