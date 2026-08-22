import type { ResultExportLifecycle } from './use-result-export-lifecycle';

const PHASE_LABELS: Record<ResultExportLifecycle['phase'], string> = {
  prepare: 'Đang chuẩn bị',
  query: 'Đang đọc dữ liệu',
  load_template: 'Đang mở mẫu Excel',
  build_rows: 'Đang dựng dữ liệu',
  write_rows: 'Đang ghi dữ liệu',
  format: 'Đang định dạng',
  save: 'Đang lưu file',
  completed: 'Đã hoàn tất',
};

export function ResultExportProgressBar({ lifecycle }: { lifecycle: ResultExportLifecycle }) {
  const percent = Math.max(0, Math.min(100, lifecycle.percent));
  return (
    <div className="result-export-progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(percent)}>
      <div className="result-export-progress-copy">
        <small>{PHASE_LABELS[lifecycle.phase]}</small>
        <strong>{Math.round(percent)}%</strong>
      </div>
      <div className="result-export-progress-track"><span style={{ width: `${percent}%` }} /></div>
    </div>
  );
}
