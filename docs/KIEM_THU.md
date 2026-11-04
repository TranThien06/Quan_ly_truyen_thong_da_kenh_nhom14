# Kết quả kiểm tra bản Python FastAPI

Ngày kiểm tra: 04/11/2026. Môi trường: Windows, Python 3.14.5, MySQL Community 8.0.45, database riêng `team14_api_test_python` trên cổng 3308. Không kiểm thử bằng database chính của nhóm hoặc mạng xã hội thật.

## Kiểm thử chuyển đổi

- Đối chiếu đủ 90 method/URL nghiệp vụ và mã phản hồi thành công của Express. FastAPI công bố thêm webhook/health/ready trong OpenAPI, tổng 94 operation.
- Đọc bcrypt hash do Node.js tạo; đăng nhập được tài khoản dùng hash cũ.
- Giải mã đúng fixture AES-GCM IV + tag + ciphertext của Node.js; phát hiện sửa byte và dùng IV ngẫu nhiên khi mã hóa mới.
- Giữ ID BIGINT dạng chuỗi, thời gian lưu UTC, token phiên ngẫu nhiên/SHA-256.
- Swagger và OpenAPI hoạt động; validation không đưa mật khẩu nhập sai vào response lỗi.
- Chặn request JSON quá dung lượng, cả khi không có Content-Length.
- Parser migration xử lý comment và dấu chấm phẩy trong SQL string.

## Kiểm thử MySQL và nghiệp vụ

14 test integration thực hiện nhiều thao tác HTTP/worker:

1. Đăng ký, token xác minh một lần, profile, login/logout, khóa sau 5 lần sai.
2. Cô lập workspace, bảo vệ owner/Admin, chặn manager tự nâng thành Admin.
3. OAuth state một lần, chọn tài khoản/reconnect, không lộ token; upload ảnh và signed URL; chặn vòng lặp folder.
4. Quyền đăng, khóa chỉnh sửa khi chờ duyệt, duyệt và đăng thành công.
5. Hai request/worker đồng thời không tạo hai bài thành công trên một variant.
6. Lịch hủy không chạy; hai lần quét lịch không tạo job trùng.
7. Retry có backoff, dừng sau lần đầu và 3 lần thử lại.
8. Timeout mơ hồ/worker hết lease cần đối soát; chặn retry mù và kiểm tra mã bài đối soát.
9. Idempotency-Key khi tạo bài; từ chối tham chiếu chéo workspace.
10. Chặn tự duyệt; sửa nội dung mất hiệu lực duyệt.
11. Đồng bộ inbox không tạo trùng, reply có request_id, xuất CSV/XLSX/PDF, thông báo và audit.
12. Snapshot/checkpoint chỉ số không lặp và refresh token demo.
13. Reset mật khẩu một lần, thu hồi phiên cũ, dùng bcrypt hash legacy.
14. Chữ ký webhook bắt buộc và payload lặp không xử lý lại.

Tổng bộ test: **22 test** (8 unit/contract và 14 integration). Source được Ruff kiểm tra và định dạng, các thư viện được khóa phiên bản. `pip check` không có dependency hỏng; `pip-audit` chưa phát hiện lỗ hổng đã biết tại thời điểm kiểm tra.

Migration 001/002 chạy được trên DB trống; chạy lại nhận version đã áp dụng. Không có bước drop/recreate database chính khi chuyển từ Express. Test integration làm sạch riêng các fixture trong DB có tiền tố kiểm thử; không được dùng database kiểm thử để lưu dữ liệu thật.

## Giới hạn của kết quả

Chưa nghiệm thu gọi Facebook/Instagram/TikTok thật, SMTP thật, video FFmpeg, Docker build, GitHub CI, benchmark hoặc khôi phục backup production. Chuyển ngôn ngữ không làm các quyền nền tảng tự được cấp. Các giới hạn live giữ như bản trước và được ghi ở PHAM_VI.md.

Không suy ra mọi tiêu chí SRS đều đã nghiệm thu chỉ vì bộ test này đạt. Các fixture có chữ Express/Node là dữ liệu demo cố định phục vụ kiểm thử tương thích, không phải runtime JavaScript hay thông tin bí mật thật.
