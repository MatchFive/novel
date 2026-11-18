"""run 包全局常量（计划附录 C.1 逐字值）。"""

# Agent 循环
MAX_STEPS = 60
MAX_NOOP_STREAK = 6
COOLDOWN_STEPS = 3
MAX_PARALLEL = 6
STEP_PLAN_MAX_STEPS = 12

# token 预算（P1 员工主循环）：累计超线即暂停
BUDGET_MAX_TOKENS = 2_000_000

# 观测裁剪
OBS_MAX = 1500
OBS_SOFT_LINE = 240
OBS_HARD_BATCH = 80

# 事件扫描
SCAN_CAP = 2_000_000
WORK_MEMORY_REPLAY = 12

# events.log 轮转
EVENTS_ROTATE_BYTES = 50 * 1024 * 1024
EVENTS_ROTATE_LINES = 50_000

# 瞬时失败标记（命中不熔断，观测加 FAILED-TRANSIENT: 前缀）
TRANSIENT_MARKERS = ("timeout", "timed out", "超时", "connection", "连接", "截断")
