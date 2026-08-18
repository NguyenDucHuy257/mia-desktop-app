# Phase 6 — Activation

Trạng thái: `SKIPPED` theo yêu cầu hiện tại.

## Giới hạn được chấp nhận

- Không revoke thiết bị từ xa.
- Không có device-limit đáng tin cậy.
- Không có refresh-token rotation hoặc reuse detection.
- License hoàn toàn offline có thể bị sao chép hoặc patch.

Nếu cần các thuộc tính bảo mật trên, phải thiết kế activation server riêng. Không được giả lập server rồi tuyên bố đạt challenge, revoke hoặc rotation test.
