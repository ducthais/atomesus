'use client';

import dynamic from 'next/dynamic';
import { useState } from 'react';
import { FileCode2 } from 'lucide-react';
import type { FileChange } from '@/lib/catalog/types';

const DiffEditor = dynamic(() => import('@monaco-editor/react').then(m => m.DiffEditor), { ssr: false });
const LANGUAGE: Record<string, string> = { py: 'python', sql: 'sql', yml: 'yaml', yaml: 'yaml' };
const STATUS = { added: ['Thêm', 'good'], removed: ['Xóa', 'bad'], changed: ['Sửa', 'wait'], same: ['Giữ nguyên', 'muted'] } as const;

/** Diff hai bên (bản trước | bản này) cho từng file sinh ra; Monaco tải từ CDN, trong lúc tải hiện danh sách dòng đổi. */
export function FileDiffViewer({ files }: { files: FileChange[] }) {
  const [selected, setSelected] = useState(files[0]?.path);
  const file = files.find(f => f.path === selected) ?? files[0];
  return <div className="diff-viewer">
    <aside className="file-tree" aria-label="File thay đổi">{files.map(f => {
      const [label, tone] = STATUS[f.status];
      return <button key={f.path} className="file" aria-current={f.path === file.path ? 'true' : undefined} onClick={() => setSelected(f.path)} title={f.path}>
        <FileCode2 size={13} aria-hidden /><span className="diff-name">{f.path.split('/').at(-1)}</span>
        <span className={`badge tone-${tone}`}>{label}</span>
        {(f.addedLines || f.removedLines) ? <small><span className="good-text">+{f.addedLines ?? 0}</span> <span className="bad-text">−{f.removedLines ?? 0}</span></small> : null}
      </button>;
    })}</aside>
    <div className="editor">
      <div className="editor-bar"><span className="mono">{file.path}</span><span className="muted">Trái: bản trước · Phải: bản này</span></div>
      <div className="editor-body">
        <DiffEditor key={file.path} height="100%" language={LANGUAGE[file.path.split('.').pop() ?? ''] ?? 'plaintext'} original={file.before ?? ''} modified={file.after ?? ''}
          loading={<pre className="plain-code">{`Đang tải trình xem diff… (+${file.addedLines ?? 0} / −${file.removedLines ?? 0} dòng)`}</pre>}
          options={{ readOnly: true, renderSideBySide: true, minimap: { enabled: false }, fontSize: 12, scrollBeyondLastLine: false, automaticLayout: true }} />
      </div>
    </div>
  </div>;
}
