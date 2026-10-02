'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Check, LoaderCircle } from 'lucide-react';

const STEPS = [
  { title: 'Lập kế hoạch', detail: 'Đọc yêu cầu và danh mục nguồn, chọn bảng, độ hạt, chỉ số.', typical: '3–5 giây' },
  { title: 'Viết Spec và qua các cổng kiểm tra', detail: 'Đọc schema + dòng mẫu thật, viết mapping cột và phép tính; Spec sai thì agent tự sửa.', typical: '5–15 giây' },
  { title: 'Sinh code và chạy thử trong sandbox', detail: 'dlt tải dữ liệu thật từ nguồn vào bronze, rồi dbt build staging + gold và chạy test.', typical: '15–20 giây' },
];

/** Bước hiện tại theo trạng thái agent ghi vào sổ bằng chứng sau mỗi bước. */
function currentStep(status: string, stage: string): number {
  if (status === 'VALIDATING') return 2;
  return stage === 'intake' ? 0 : 1;
}

/**
 * Run đang chạy nền: hiện từng bước và thời gian đã chạy, làm mới trang mỗi 2 giây cho tới khi run dừng
 * (khi đó trang server không còn render khung này nữa).
 */
export function RunProgress({ status, stage, startedAt, specRetries }: { status: string; stage: string; startedAt: string; specRetries: number }) {
  const router = useRouter();
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const tick = setInterval(() => setNow(Date.now()), 1000);
    const poll = setInterval(() => router.refresh(), 2000);
    return () => { clearInterval(tick); clearInterval(poll); };
  }, [router]);
  const step = currentStep(status, stage);
  const elapsed = Math.max(0, Math.round((now - new Date(startedAt).getTime()) / 1000));

  return <section className="panel progress-panel" aria-live="polite">
    <div className="panel-head"><h2><LoaderCircle size={16} className="spin" aria-hidden /> Agent đang chạy</h2>
      <span className="muted small">đã chạy {elapsed} giây · thường mất 25–40 giây</span></div>
    <ol className="progress-steps">
      {STEPS.map((s, i) => <li key={s.title} className={i < step ? 'done' : i === step ? 'active' : 'todo'}>
        <span className="progress-dot" aria-hidden>{i < step ? <Check size={13} /> : i === step ? <LoaderCircle size={13} className="spin" /> : i + 1}</span>
        <div><strong>{s.title}</strong> <span className="muted small">· {s.typical}</span>
          <p className="muted small">{s.detail}</p>
          {i === 1 && i === step && specRetries > 0 && <p className="small">Spec lần {specRetries} bị cổng kiểm tra trả lại, agent đang sửa.</p>}
        </div>
      </li>)}
    </ol>
    <p className="muted small">Có thể rời trang này: agent vẫn chạy và kết quả được ghi vào sổ bằng chứng. Nếu bạn chọn xem mapping trước, agent dừng sau bước 2 để bạn kiểm tra.</p>
  </section>;
}
