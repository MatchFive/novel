import { ReactNode, ButtonHTMLAttributes } from "react";

type ButtonVariant = "primary" | "ghost" | "subtle";

export function Button({
  children,
  variant = "ghost",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant }) {
  const base =
    "inline-flex items-center justify-center rounded-lg px-3 py-1.5 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-40 ";
  const variants: Record<ButtonVariant, string> = {
    primary: "bg-accent text-paper shadow-soft hover:bg-accent-strong ",
    ghost: "border border-ink/25 text-ink hover:bg-surface-2 ",
    subtle: "text-ink-soft hover:bg-surface-2 ",
  };
  return (
    <button {...props} className={base + variants[variant] + (props.className || "")}>
      {children}
    </button>
  );
}

export function Input(props: any) {
  return (
    <input
      {...props}
      className={
        "w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink outline-none transition focus:border-accent focus:ring-1 focus:ring-accent/30 " +
        (props.className || "")
      }
    />
  );
}

export function Textarea(props: any) {
  return (
    <textarea
      {...props}
      className={
        "w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink outline-none transition focus:border-accent focus:ring-1 focus:ring-accent/30 " +
        (props.className || "")
      }
    />
  );
}

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <div className={"rounded-lg border border-line bg-surface shadow-soft " + className}>{children}</div>
  );
}

export function SectionTitle({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <div className={"border-l-2 border-accent pl-2 font-serif text-base font-medium text-ink " + className}>
      {children}
    </div>
  );
}

export function Tag({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <span className={"rounded-full bg-accent-soft px-2 py-0.5 text-xs font-medium text-accent-strong " + className}>
      {children}
    </span>
  );
}

export function Empty({ text }: { text: string }) {
  return <div className="px-4 py-10 text-center text-sm text-muted">{text}</div>;
}
