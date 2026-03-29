import "./globals.css";
import { ClientShell } from "@/components/ClientShell";

export const metadata = {
  title: "SuzyCloud Dashboard",
  description: "Multi-Tenant WhatsApp AI Assistant Platform",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="min-h-screen antialiased">
        <ClientShell>{children}</ClientShell>
      </body>
    </html>
  );
}
