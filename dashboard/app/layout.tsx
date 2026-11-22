import "./globals.css";
import type { ReactNode } from "react";

export const metadata = { title: "SkillForge", description: "Skill synthesis research dashboard" };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="top">
          <a href="/" className="brand">SkillForge</a>
          <span className="muted">evaluation-driven skill synthesis</span>
        </header>
        <main>{children}</main>
      </body>
    </html>
  );
}
