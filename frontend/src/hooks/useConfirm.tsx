import { useState, useCallback } from "react";
import { ConfirmDialog } from "@/components/ui";

interface ConfirmState {
  open: boolean;
  title: string;
  message: string;
  resolve: ((value: boolean) => void) | null;
}

export function useConfirm() {
  const [state, setState] = useState<ConfirmState>({
    open: false,
    title: "",
    message: "",
    resolve: null,
  });

  const confirm = useCallback((title: string, message: string): Promise<boolean> => {
    return new Promise((resolve) => {
      setState({ open: true, title, message, resolve });
    });
  }, []);

  const handleClose = (value: boolean) => {
    state.resolve?.(value);
    setState((s) => ({ ...s, open: false, resolve: null }));
  };

  const dialog = (
    <ConfirmDialog
      open={state.open}
      title={state.title}
      message={state.message}
      onConfirm={() => handleClose(true)}
      onCancel={() => handleClose(false)}
    />
  );

  return { confirm, dialog };
}
