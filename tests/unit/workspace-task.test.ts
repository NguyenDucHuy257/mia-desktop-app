import { describe, expect, it } from 'vitest';
import { workspaceTaskConflictMessage } from '../../src/lib/workspace-task';

describe('workspace task admission', () => {
  it('blocks a different data-writing workflow with a useful notice', () => {
    expect(workspaceTaskConflictMessage('sync', 'artifact-download')).toBe(
      'Đang đồng bộ dữ liệu hóa đơn. Vui lòng chờ tiến trình hoàn tất hoặc dừng tiến trình hiện tại trước khi tải XML/HTML/PDF.',
    );
  });

  it('does not block the owner of the active workflow', () => {
    expect(workspaceTaskConflictMessage('artifact-download', 'artifact-download')).toBeNull();
    expect(workspaceTaskConflictMessage(null, 'result-export')).toBeNull();
  });
});
