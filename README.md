# API quản lý truyền thông đa kênh — Nhóm 14

Backend **Python + FastAPI + MySQL**. Các module xử lý tài khoản, workspace, kênh, media, bài đăng, duyệt bài, lịch, báo cáo, hộp thư và thông báo/quản trị. Mặc định chạy demo, không đăng bài thật.

## Chạy trên Windows

Yêu cầu Python 3.12 trở lên (đã kiểm thử bằng Python 3.14) và MySQL 8 đang chạy. Mở PowerShell tại thư mục `Code_code`:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m scripts.setup
```

Nếu đã có `.env` từ bản Express, **giữ nguyên file và TOKEN_ENCRYPTION_KEY**. Setup không ghi đè `.env`. Nếu mới tạo, điền DB_HOST/DB_PORT/DB_USER/DB_PASSWORD/DB_NAME. Không cần kích hoạt venv hay thay ExecutionPolicy; các lệnh ở đây gọi trực tiếp Python trong venv.

```powershell
.\.venv\Scripts\python.exe -m scripts.migrate
.\.venv\Scripts\python.exe -m app.server
```

Mở terminal thứ hai, cũng tại thư mục dự án:

```powershell
.\.venv\Scripts\python.exe -m app.worker
```

Giữ cả hai terminal mở. Nhấn Ctrl+C để dừng từng tiến trình. Nếu server Express cũ đang chiếm cổng 3000, dừng nó trước khi chạy FastAPI.

- Swagger để thử API: [http://localhost:3000/docs](http://localhost:3000/docs)
- API hoạt động: [http://localhost:3000/health](http://localhost:3000/health)
- MySQL kết nối được: [http://localhost:3000/ready](http://localhost:3000/ready)
- OpenAPI: [http://localhost:3000/openapi.json](http://localhost:3000/openapi.json)
- Base URL: `http://localhost:3000/api/v1`

## Giao diện workspace

Mở [http://localhost:3000](http://localhost:3000) sau khi chạy server. Giao diện Orbitspace dùng tông nâu chocolate và tím lilac, với hình UFO chủ đạo, hỗ trợ điện thoại, đăng nhập bằng tài khoản đã xác minh, tìm kiếm, tạo và cập nhật workspace. Không cần Node.js hay bước build frontend.

Tên và đường dẫn là bắt buộc; đường dẫn chỉ gồm chữ thường không dấu, số và dấu gạch ngang. Múi giờ mặc định là Việt Nam. Sau khi lưu, danh sách hiển thị dữ liệu trả về từ API/MySQL. Đường dẫn logo được lưu trong thông tin workspace; thẻ danh sách dùng chữ viết tắt của tên.

Giao diện lấy quyền thực tế qua `GET /api/v1/workspaces/{workspace_id}/access`. Chỉ thành viên có `workspace.manage` được bật biểu mẫu cập nhật; các thành viên khác chỉ xem. API tiếp tục kiểm tra quyền khi lưu, kể cả khi quyền thay đổi sau khi mở biểu mẫu. Phiên đăng nhập được giữ trong `sessionStorage` của tab và xóa khi đăng xuất hoặc API trả về phiên hết hạn.

Trong Swagger: đăng ký → lấy token xác minh từ email demo → xác minh → đăng nhập → bấm Authorize và điền access_token → tạo workspace. `MAIL_MODE=file` ghi email vào `storage/mail` khi worker chạy. Xem [hướng dẫn payload](docs/API.md) hoặc import [Postman collection](docs/Team14.postman_collection.json).

Trên Linux/macOS, thay `.\.venv\Scripts\python.exe` bằng `.venv/bin/python`.

## File chính và cách đọc code

### Thành viên workspace

Trong giao diện, mở một workspace rồi chọn **Thành viên**. Mọi thành viên đang hoạt động có thể xem/tìm tên, email, vai trò, trạng thái và ngày tham gia. Người có `member.manage` được mời theo email, đổi vai trò, loại thành viên và gửi lại lời mời đang chờ/hết hạn. Thao tác đổi vai trò hoặc loại thành viên có bước xác nhận. Chủ sở hữu không thể bị loại/hạ quyền; chỉ Admin được quản lý vai trò Admin.

API: `GET /api/v1/workspaces/{id}/members`, `PATCH`/`DELETE /api/v1/workspaces/{id}/members/{member_id}`, `GET`/`POST /api/v1/workspaces/{id}/invitations`, `POST /api/v1/workspaces/{id}/invitations/{invitation_id}/resend`. Gửi lại thay token và gia hạn 7 ngày, liên kết cũ hết hiệu lực. Quyền được đọc lại từ database mỗi request, nên thay vai trò hoặc loại thành viên có hiệu lực ở request kế tiếp trong cùng phiên đăng nhập.

Email mời được xử lý bởi worker. Với `MAIL_MODE=file`, xem email tại `storage/mail`; chế độ SMTP cần cấu hình SMTP tương ứng. Liên kết dùng `APP_URL/accept-invitation?token=...`; người nhận đăng nhập bằng tài khoản đã xác minh có email trùng lời mời, rồi chọn **Chấp nhận lời mời**. Đặt `APP_URL` theo địa chỉ mà người nhận truy cập được.

```text
app/
  main.py           FastAPI, Swagger, gắn các router, xử lý lỗi
  server.py         Khởi động HTTP server qua Uvicorn
  worker.py         Tiến trình chạy việc nền riêng
  config.py         Đọc .env; tương thích NODE_ENV cũ và APP_ENV mới
  db.py             Connection UTC, transaction và SQL tham số hóa
  dependencies.py   Xác thực Bearer, membership và permission
  security.py       bcrypt, SHA-256 và AES-GCM tương thích Express
  common.py         ID, phân trang, thời gian và response {data}
  events.py         Audit, thông báo và lịch sử trạng thái
  middleware.py     Giới hạn dung lượng request kể cả chunked upload
  publishing.py     Quy tắc chung cho đăng bài và lịch
  social.py         Adapter Facebook/Instagram/TikTok, chế độ mock
  webhooks.py       Kiểm tra chữ ký và chống lặp webhook Meta
  modules/
    auth.py         Đăng ký, email, phiên, mật khẩu và hồ sơ
    workspaces.py   Workspace, thành viên, lời mời và cấu hình
    channels.py     OAuth, chọn tài khoản, reconnect/disconnect/sync
    posts.py        Soạn, nhân bản, preview, duyệt và bình luận
    schedules.py    Đăng ngay, lên lịch, đổi/hủy, retry và đối soát
    media.py        Upload, thumbnail, thư mục, nhãn và signed URL
    reports.py      Chỉ số, xếp hạng, yêu cầu và tải báo cáo
    inbox.py        Hội thoại, phân công và reply có request_id
    notifications.py Thông báo, số chưa đọc và tùy chọn nhận
    admin.py        Nhật ký chỉ đọc và cấu hình toàn hệ thống
  jobs/             Đăng bài, email, xuất file, đồng bộ
scripts/            Setup, migration và xuất OpenAPI
tests/              Unit, contract và MySQL integration tests
database/           Migration 002; schema 001 vẫn nằm cùng tài liệu SRS
tai-lieu-da-dong-bo/ SRS, bảng task và schema gốc cần cho migration
```

Mỗi file nghiệp vụ dùng `APIRouter`, model Pydantic kiểm tra input, rồi gọi SQL thông qua `with transaction() as db`. Các hàm dùng chung được tách khi API và worker cùng cần. Không dùng ORM hay lớp repository phức tạp; SQL đặt cạnh nghiệp vụ để nhóm dễ theo dõi. Tất cả SQL nhận dữ liệu người dùng qua `%s`, không nối dữ liệu vào câu lệnh.

## Chuyển từ Express mà không mất dữ liệu

- Giữ nguyên database, `.env`, khóa mã hóa và thư mục `storage`.
- Migration nhận đúng các version `001_initial`/`002_runtime` cũ; database đã migrate trả `Already applied ...`, không tạo lại bảng và không xóa dữ liệu.
- Bcrypt hash, token phiên SHA-256 và layout AES-GCM cũ được đọc bởi Python. Đã kiểm thử bằng fixture tạo từ Node.js, không phải dữ liệu người dùng thật.
- Giữ tương ứng 90 method/URL nghiệp vụ và các mã thành công. FastAPI còn công bố webhook, health và ready, tổng cộng 94 operation trong OpenAPI.
- ID vẫn trả dạng chuỗi; dữ liệu JSON/NULL giữ ý nghĩa cũ. Một số chi tiết validation, tên placeholder trong OpenAPI và kiểu số của chỉ số có thể khác; frontend nên xử lý chỉ số bằng Number và dữ liệu thiếu bằng null.
- Node.js, npm, Express, package.json và node_modules không còn cần để chạy dự án.
- Các commit Express cũ vẫn có trong lịch sử Git. Không chạy lại script SQL thủ công trên database đang có bảng.

## Kiểm thử và định dạng

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check app scripts tests
.\.venv\Scripts\python.exe -m ruff format --check app scripts tests
```

Mặc định chỉ chạy test không cần MySQL; integration tests bị bỏ qua. Muốn chạy integration, tạo `.env.test` riêng với DB_NAME bắt đầu `team14_api_test`, APP_ENV=test, SOCIAL_MODE=mock, MAIL_MODE=file, STORAGE_DIR=.runtime/test-storage và khóa mã hóa test riêng. Sau đó:

```powershell
$env:ENV_FILE=".env.test"
.\.venv\Scripts\python.exe -m scripts.migrate
.\.venv\Scripts\python.exe -m pytest --integration -q
Remove-Item Env:ENV_FILE
```

Integration test **xóa fixture trong database kiểm thử**, không dùng tên DB thực. Guard từ chối DB ngoài tiền tố test và chế độ live. Dữ liệu upload test phải nằm trong thư mục test riêng. Kết quả chi tiết nằm ở [KIEM_THU.md](docs/KIEM_THU.md).

Xuất lại tài liệu máy đọc được sau khi đổi route/model:

```powershell
.\.venv\Scripts\python.exe -m scripts.export_openapi
```

## Video, PDF, Docker và live

Video MP4 cần FFmpeg và FFprobe trong PATH hoặc cấu hình FFMPEG_PATH/FFPROBE_PATH. PDF tiếng Việt dùng REPORT_FONT_PATH, mặc định Windows là `C:/Windows/Fonts/arial.ttf`, Linux là DejaVuSans.ttf.

Docker và Compose đã chuyển sang Python:

```powershell
docker compose up --build
```

Điền DB_PASSWORD trong `.env` trước. Compose tạo MySQL riêng, migration, API, worker; MySQL expose localhost:3307. Chưa chạy Docker/CI trong môi trường này. Không thay thế MySQL đang có dữ liệu của nhóm bằng container trống mà chưa di chuyển dữ liệu.

Các giới hạn tích hợp live giữ như bản trước, xem [PHAM_VI.md](docs/PHAM_VI.md). Chưa nghiệm thu Facebook/Instagram/TikTok thật; cần App ID/secret, quyền API, HTTPS và domain media được duyệt. URL có `example.com/demo` là mô phỏng. Production bắt buộc SOCIAL_MODE=live, MAIL_MODE=smtp và APP_URL HTTPS. Hệ thống dùng phiên trong header Bearer, không dùng cookie/JWT mặc định.

## Tài liệu kỹ thuật chính thức

- [FastAPI nhiều module](https://fastapi.tiangolo.com/tutorial/bigger-applications/)
- [FastAPI kiểm thử](https://fastapi.tiangolo.com/tutorial/testing/)
- [Cryptography AES-GCM](https://cryptography.io/en/stable/hazmat/primitives/aead/)
- [Meta Page publishing](https://developers.facebook.com/docs/pages-api/posts/)
- [Instagram Facebook Login](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-facebook-login/content-publishing/)
- [TikTok Direct Post](https://developers.tiktok.com/doc/content-posting-api-get-started/)
