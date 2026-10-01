import { LIMITS, STAGES, emptyChecks, newVersion, passedChecks, type Check, type Log, type Run } from './studio.ts';

export type RunEvent = { type: 'stage' | 'check' | 'status' | 'log' | 'repair'; stage?: number; checks?: Check[]; status?: Run['status']; log?: Log; sqlRetries?: number; llmCalls?: number };
export type RunSubscription = { cancel: () => void };
export interface PipelineApi {
  subscribeToRun(run: Run, onEvent: (event: RunEvent) => void): RunSubscription;
}

/** Deterministic SSE-shaped demo adapter. Replace with EventSource for the real API. */
export const mockPipelineApi: PipelineApi = {
  subscribeToRun(run, onEvent) {
    let cancelled = false;
    const timers: ReturnType<typeof setTimeout>[] = [];
    const emit = (delay: number, event: RunEvent) => {
      timers.push(setTimeout(() => { if (!cancelled) onEvent(event); }, delay));
    };
    const log = (stage: number, level: Log['level'], message: string, detail?: string): RunEvent => ({
      type: 'log', log: { id: `${run.id}-${run.version}-${stage}-${message}`, stage, level, message, detail, at: new Date().toISOString() },
    });
    const stopAt = run.scenario === 'semantic' ? 3 : run.scenario === 'budget' ? 8 : run.scenario === 'sql' || run.scenario === 'ast' ? 7 : 14;
    const startStage = Math.min(run.stage, stopAt);
    for (let stage = startStage; stage <= stopAt; stage++) {
      const delay = 320 + (stage - startStage) * 260;
      emit(delay, { type: 'stage', stage, llmCalls: Math.min(LIMITS.llmCalls, [0, 1, 1, 1, 2, 2, 3, 3, 3, 3, 3, 4, 4, 5, 5][stage]) });
      emit(delay + 55, log(stage, 'info', STAGES[stage] + ' · chạy trong sandbox mô phỏng'));
    }
    const finish = 430 + (stopAt - startStage) * 260;
    if (run.scenario === 'ast') {
      emit(finish, log(7, 'error', 'AST policy chặn SELECT * trong gold.', 'policy/budgets.yml: forbid_select_star_in: [gold]. Hãy chọn cột tường minh trong kế hoạch.'));
      emit(finish + 50, { type: 'status', status: 'BLOCKED_AST' });
    } else if (run.scenario === 'sql') {
      emit(finish, log(7, 'error', 'Cột total_amount không tồn tại.', 'AST/SQL: sales_raw.orders có cột amount.'));
      emit(finish + 80, { type: 'repair', log: { id: `${run.id}-${run.version}-repair`, stage: 7, level: 'success', message: 'Đã sửa total_amount → amount, tạo version mới và kiểm tra lại.', at: new Date().toISOString() } });
    } else if (run.scenario === 'semantic') {
      emit(finish, log(3, 'error', 'Định nghĩa customer giữa Sales và Marketing xung đột.', 'policy/domains.yml: sales.customer là tài khoản hợp đồng; marketing.customer là người để lại email.'));
      emit(finish + 50, { type: 'status', status: 'BLOCKED_SEMANTIC_CONFLICT' });
    } else if (run.scenario === 'budget') {
      emit(finish, log(8, 'error', 'Dự tính quét 8,2 GB > trần 5 GB.', 'Chi phí ước tính $0,0513 > $0,05. Cần thêm partition filter.'));
      emit(finish + 50, { type: 'status', status: 'BLOCKED_BUDGET' });
    } else {
      const checks = passedChecks();
      if (run.scenario === 'metric') {
        checks[5] = { ...checks[5], state: 'fail', value: 'Lệch 2,4%', detail: 'metric_reconcile fail · minh họa' };
      }
      emit(finish, { type: 'check', checks: checks.map(c => ({ ...c, state: 'running' as const, value: '—', detail: 'Đang đo trong sandbox mô phỏng' })) });
      emit(finish + 250, { type: 'check', checks });
      emit(finish + 275, log(14, run.scenario === 'metric' ? 'error' : 'success', run.scenario === 'metric' ? 'metric_reconcile fail: tổng doanh thu lệch 2,4%.' : 'RunReport: mọi check đạt. Có thể chuyển Reviewer.', run.scenario === 'metric' ? 'Doanh thu kỳ vọng 1.250.000.000 VND; kết quả 1.220.000.000 VND.' : 'Đối soát dòng, idempotent, dbt tests, chi phí, hiệu năng và doanh thu đều đạt.'));
      emit(finish + 320, { type: 'status', status: run.scenario === 'metric' ? 'NEEDS_HUMAN' : 'READY_FOR_REVIEW' });
    }
    return { cancel: () => { cancelled = true; timers.forEach(clearTimeout); } };
  },
};

export function applyRunEvent(run: Run, event: RunEvent): Run {
  const now = new Date().toISOString();
  if (event.type === 'repair') {
    const revised = newVersion(run, 'success');
    return { ...revised, sqlRetries: run.sqlRetries + 1, llmCalls: run.llmCalls, logs: [...run.logs, event.log!], updatedAt: now };
  }
  if (event.type === 'stage') return { ...run, stage: event.stage ?? run.stage, llmCalls: event.llmCalls ?? run.llmCalls, sqlRetries: event.sqlRetries ?? run.sqlRetries, updatedAt: now };
  if (event.type === 'check') return { ...run, checks: event.checks ?? emptyChecks(), updatedAt: now };
  if (event.type === 'log') return { ...run, logs: [...run.logs, event.log!], updatedAt: now };
  if (event.type === 'status') return { ...run, status: event.status ?? run.status, updatedAt: now };
  return run;
}
