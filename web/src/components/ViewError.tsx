/// 视图级错误态（Task 20 打磨）：API 加载失败不白屏——错误文案 + 重试按钮。

interface ViewErrorProps {
  message: string;
  onRetry: () => void;
}

export function ViewError({ message, onRetry }: ViewErrorProps) {
  return (
    <div className="view view-error">
      <p className="error-text">加载失败：{message}</p>
      <button type="button" className="btn-ghost" onClick={onRetry}>
        重试
      </button>
    </div>
  );
}
