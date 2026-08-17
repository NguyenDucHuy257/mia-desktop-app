# Kế hoạch MIA Desktop chạy cục bộ

Roadmap mới chuyển ứng dụng sang mô hình chạy crawler và xử lý artifact trực tiếp trên máy người dùng, không gọi HTTP API nghiệp vụ.

```text
React renderer -> IPC allowlist -> Electron main -> JSON-RPC stdin/stdout
                                                -> Python child process
                                                -> SQLite + crawler + artifacts
```

Renderer không được chạy crawler, đọc credential hoặc truy cập filesystem trực tiếp. Credential được Electron main mã hóa bằng `safeStorage`/Windows DPAPI. Không nhập deployment script hoặc secret từ backend vào desktop.

## Tài liệu từng phase

| Phase | Tài liệu | Gate chính |
|---|---|---|
| 0 | [Kiến trúc và kiểm kê](offline-phases/PHASE-00-ARCHITECTURE.md) | dependency/license/secret audit và prototype JSON-RPC |
| 1 | [Python runtime và SQLite](offline-phases/PHASE-01-RUNTIME.md) | runtime, migration và phục hồi sau restart |
| 2 | [Tài khoản cục bộ](offline-phases/PHASE-02-ACCOUNTS.md) | credential mã hóa và portal login thật |
| 3 | [Job lifecycle offline](offline-phases/PHASE-03-JOBS.md) | state machine, idempotency, resume và cancel |
| 4 | [Overview/detail](offline-phases/PHASE-04-RESULTS.md) | regression 421 items và visual Figma |
| 5 | [XML/HTML/PDF/Excel](offline-phases/PHASE-05-ARTIFACTS.md) | integrity, filesystem security và tải hàng loạt |
| 6 | [Activation](offline-phases/PHASE-06-ACTIVATION.md) | bỏ qua; ghi nhận giới hạn bảo vệ offline |
| 7 | [Installer production](offline-phases/PHASE-07-RELEASE.md) | Windows sạch, update/rollback và signing |
| 8 | [Acceptance/soak](offline-phases/PHASE-08-ACCEPTANCE.md) | full workflow thật và kiểm tra độ ổn định |

Xem [quy tắc chung và thứ tự triển khai](offline-phases/README.md). Roadmap API cũ không còn là kế hoạch mục tiêu sau khi roadmap này được duyệt và merge.
