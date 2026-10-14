import { uploadError, type UserUpload } from './uploads.ts';
import type { ConnectionResult } from './user-connections.ts';

export function sendUpload(files: File[], progress: (percent: number) => void): Promise<ConnectionResult<UserUpload>> {
  return new Promise(resolve => {
    const request = new XMLHttpRequest();
    const form = new FormData();
    files.forEach(file => form.append('files', file));
    request.open('POST', '/connections/upload');
    request.responseType = 'json';
    request.upload.onprogress = event => { if (event.lengthComputable) progress(Math.round(event.loaded / event.total * 100)); };
    request.onload = () => resolve(request.status === 201 ? { data: request.response as UserUpload }
      : { error: uploadError(request.status, request.response) });
    request.onerror = () => resolve({ error: 'Mất kết nối khi tải file. Vui lòng thử lại.' });
    request.onabort = () => resolve({ error: 'Đã dừng tải file.' });
    request.send(form);
  });
}
