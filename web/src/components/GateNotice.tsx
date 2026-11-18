/// ⛔ 写作闸硬拦卡（§4.7）：「NOPROGRESS: write_plot 未执行」开头的 tool_result 不用
/// 普通工具卡渲染，改用琥珀大卡片——观测文案原样给人读（后端已按 §4.7 措辞生成：
/// 谁没卡 / 哪章被拦 / 按你的规矩先建卡再写正文），前端**不解析**内容；
/// 底部 [去角色视图] 按钮跳 characters 视图建卡，两张卡建完闸自动解除。

import { useNavigation } from "../state/navigation";

/** 闸观测前缀（后端钉死；前端只认前缀，不解析后续文案）。 */
export const GATE_NOTICE_PREFIX = "NOPROGRESS: write_plot 未执行";

export function isGateObservation(observation: string): boolean {
  return observation.startsWith(GATE_NOTICE_PREFIX);
}

export function GateNotice({ observation, seq }: { observation: string; seq: number }) {
  const { navigate } = useNavigation();
  return (
    <div className="stream-card gate-notice">
      <div className="stream-card-title">
        ⛔ 写不了：角色还没有角色卡
        <span className="muted"> · seq {seq}</span>
      </div>
      <pre className="gate-notice-body">{observation}</pre>
      <div className="gate-notice-actions">
        <button type="button" className="btn-ghost" onClick={() => navigate("characters")}>
          去角色视图
        </button>
        <span className="muted gate-notice-hint">没有「跳过」按钮 —— 硬拦。卡建完闸自动解除。</span>
      </div>
    </div>
  );
}
