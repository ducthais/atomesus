'use client';

import { useState } from 'react';
import { Lock } from 'lucide-react';
import type { PolicyResponse } from '@/lib/types';

/** FR-19: ba file chính sách, chỉ đọc. Con người sửa qua git, không sửa từ app. */
export function PolicyView({ policy }: { policy: PolicyResponse | null }) {
  const names = Object.keys(policy?.files ?? {});
  const [active, setActive] = useState<string | null>(null);
  const current = active ?? names[0] ?? null;
  if (!policy || !current) return <div className="empty">Không tải được /policy.</div>;
  return (
    <div className="policy">
      <div className="tabs">
        {names.map((n) => (
          <button key={n} className={`tab ${n === current ? 'active' : ''}`} onClick={() => setActive(n)}><span className="mono">{n}</span></button>
        ))}
      </div>
      <div className="note">
        <Lock size={13} />Chỉ đọc. Chỉ steward sửa qua git. Trần hiện hành: sửa spec {policy.limits.spec} · sửa SQL {policy.limits.sql} · gọi LLM {policy.limits.llm}.
      </div>
      <pre>{policy.files[current].split('\n').map((l, i) => <span key={i}>{l || ' '}</span>)}</pre>
    </div>
  );
}
