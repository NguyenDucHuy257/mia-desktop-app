export type WorkspaceTask = 'sync' | 'artifact-download' | 'result-export' | 'vat-return-export';

const taskLabels: Record<WorkspaceTask, string> = {
  sync: 'đồng bộ dữ liệu hóa đơn',
  'artifact-download': 'tải XML/HTML/PDF',
  'result-export': 'xuất kết quả Excel',
  'vat-return-export': 'xuất tờ khai thuế GTGT',
};

export function workspaceTaskConflictMessage(active: WorkspaceTask | null, requested: WorkspaceTask) {
  if (!active || active === requested) return null;
  return `Đang ${taskLabels[active]}. Vui lòng chờ tiến trình hoàn tất hoặc dừng tiến trình hiện tại trước khi ${taskLabels[requested]}.`;
}
