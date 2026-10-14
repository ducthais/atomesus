/** Chuyển tiếp stream multipart, giữ cookie phiên và lỗi tiếng Việt; không chịu trần 1 MB của Server Action. */
export async function proxyUpload(request: Request, backend: string, token: string, fetcher: typeof fetch = fetch): Promise<Response> {
  const contentType = request.headers.get('content-type') ?? '';
  if (!contentType.toLowerCase().startsWith('multipart/form-data;') || !request.body) {
    return Response.json({ detail: 'Chọn file CSV hoặc XLSX để tải lên.' }, { status: 422 });
  }
  try {
    const init: RequestInit & { duplex: 'half' } = {
      method: 'POST', body: request.body, duplex: 'half', cache: 'no-store', signal: request.signal,
      headers: { 'Content-Type': contentType, Cookie: `pipeline_session=${token}` },
    };
    const response = await fetcher(`${backend}/api/v1/uploads`, init);
    const body = await response.json().catch(() => ({ detail: 'Backend không trả kết quả tải file hợp lệ.' }));
    return Response.json(body, { status: response.status, headers: { 'Cache-Control': 'no-store' } });
  } catch {
    return Response.json({ detail: 'Không kết nối được backend để tải file. Vui lòng thử lại.' }, { status: 503 });
  }
}
