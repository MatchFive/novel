import { ReactNode } from "react";

export default function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-full flex-col bg-paper text-ink">
      <header className="flex h-14 shrink-0 items-center justify-between border-b border-accent/30 bg-paper px-6">
        <div className="flex items-baseline gap-2">
          <span className="font-serif text-lg font-semibold tracking-wide text-ink">NOVEL STUDIO</span>
          <span className="text-xs text-muted">小说创作助手</span>
        </div>
        <nav className="flex items-center gap-6 text-sm">
          <a href="/" className="text-ink-soft transition-colors hover:text-accent">项目</a>
          <a href="/settings" className="text-muted transition-colors hover:text-accent">设置</a>
        </nav>
      </header>
      <main className="flex-1 overflow-auto bg-paper">{children}</main>
    </div>
  );
}
