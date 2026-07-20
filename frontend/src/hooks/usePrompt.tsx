import { useState, useCallback } from "react";
import { InputDialog } from "@/components/ui";

interface PromptState {
  open: boolean;
  title: string;
  message: string;
  defaultValue: string;
  resolve: ((value: string | null) => void) | null;
}

export function usePrompt() {
  const [state, setState] = useState<PromptState>({
    open: false,
    title: "",
    message: "",
    defaultValue: "",
    resolve: null,
  });

  const prompt = useCallback((title: string, message: string, defaultValue = ""): Promise<string | null> => {
    return new Promise((resolve) => {
      setState({ open: true, title, message, defaultValue, resolve });
    });
  }, []);

  const handleClose = (value: string | null) => {
    state.resolve?.(value);
    setState((s) => ({ ...s, open: false, resolve: null }));
  };

  const dialog = (
    <InputDialog
      open={state.open}
      title={state.title}
      message={state.message}
      defaultValue={state.defaultValue}
      onConfirm={(value) => handleClose(value)}
      onCancel={() => handleClose(null)}
    />
  );

  return { prompt, dialog };
}
