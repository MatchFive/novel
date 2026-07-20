import { ReactNode, ButtonHTMLAttributes, useState } from "react";

export function ConfirmDialog({
  open,
  title,
  message,
  onConfirm,
  onCancel,
  confirmText = "确定",
  cancelText = "取消",
}: {
  open: boolean;
  title: string;
  message: string;
  onConfirm: () => void;
  onCancel: () => void;
  confirmText?: string;
  cancelText?: string;
}) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink/50 p-4">
      <div className="w-full max-w-sm rounded-lg border border-line bg-surface p-5 shadow-soft">
        <div className="font-serif text-base font-medium text-ink">{title}</div>
        <div className="mt-2 text-sm text-ink-soft">{message}</div>
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="ghost" onClick={onCancel}>{cancelText}</Button>
          <Button variant="primary" onClick={onConfirm}>{confirmText}</Button>
        </div>
      </div>
    </div>
  );
}

export function InputDialog({
  open,
  title,
  message,
  defaultValue = "",
  onConfirm,
  onCancel,
  confirmText = "确定",
  cancelText = "取消",
}: {
  open: boolean;
  title: string;
  message: string;
  defaultValue?: string;
  onConfirm: (value: string) => void;
  onCancel: () => void;
  confirmText?: string;
  cancelText?: string;
}) {
  const [value, setValue] = useState(defaultValue);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink/50 p-4">
      <div className="w-full max-w-sm rounded-lg border border-line bg-surface p-5 shadow-soft">
        <div className="font-serif text-base font-medium text-ink">{title}</div>
        <div className="mt-2 text-sm text-ink-soft">{message}</div>
        <input
          type="text"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && onConfirm(value)}
          className="mt-4 w-full rounded-lg border border-line bg-paper px-3 py-2 text-sm text-ink outline-none focus:border-accent focus:ring-1 focus:ring-accent/30"
          autoFocus
        />
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="ghost" onClick={onCancel}>{cancelText}</Button>
          <Button variant="primary" onClick={() => onConfirm(value)}>{confirmText}</Button>
        </div>
      </div>
    </div>
  );
}

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

export function Card({ children, className = "", onClick }: { children: ReactNode; className?: string; onClick?: () => void }) {
  return (
    <div onClick={onClick} className={"rounded-lg border border-line bg-surface shadow-soft " + className}>{children}</div>
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
