# AI_Create_video

## Code organized by application page

```text
src/
  idea/            # 01 - Idea and script generation
  scene_design/    # 02 - Storyboard, design, Manim and visual evaluation
  preview/         # 03 - Preview, speech, subtitles and video assembly
  script_history/  # Saved script versions
  shared/          # Infrastructure shared by the four pages
```

Each page's browser code is in its own `web.js`.
See [source layout](docs/source-layout.md) for entry points and dependencies.


## AI mặc định: Codex dùng hạn mức ChatGPT

Phần tìm nguồn, viết/sửa kịch bản, thiết kế cảnh, đánh giá ảnh render và phản hồi
âm thanh dùng **Codex CLI đăng nhập ChatGPT**. Giọng đọc mặc định dùng **Microsoft Edge – Hoài Mỹ (nữ tiếng Việt)**, không cần API key.
Không tự chuyển về OpenRouter khi Codex lỗi hoặc hết hạn mức.

Cấu hình nằm ở `settings/ai_provider.json`. `AI_BACKEND`, `CODEX_MODEL`,
`CODEX_COMMAND`, `CODEX_TIMEOUT_SECONDS` trong môi trường hoặc `.env` có thể ghi đè.
Codex CLI được tìm trong PATH hoặc bản cài của extension VS Code trong WSL.
Đăng nhập bằng `codex login` nếu cần; `codex login status` phải báo ChatGPT.
Model cần được tài khoản CLI hỗ trợ; bản cài này đã kiểm tra thật với `gpt-6.1-terra`.
Cấu hình này chỉ áp dụng cho dự án, không đổi model trong cấu hình Codex cá nhân.

Các nút của workflow và bước duyệt giữ nguyên. Tệp yêu cầu, schema, ảnh đính kèm,
sự kiện JSONL và đầu ra được lưu trong `generation/.../codex_calls/<call_id>/`.
Ứng dụng ghi token do CLI báo; chi phí USD để `null`, không giả thành 0 USD.
Giao diện hiển thị số lượt Codex; hạn mức gói còn lại xem trong Codex.
API key OpenRouter không được dùng trong chế độ này. FPT là nguồn giọng tùy chọn cho các cấu hình cũ, không được dùng thay thế tự động khi Microsoft lỗi.

Tài liệu: [Codex non-interactive](https://learn.chatgpt.com/docs/non-interactive-mode),
[đăng nhập ChatGPT](https://learn.chatgpt.com/docs/auth).


Giao diện duyệt đã được làm lại theo [phác thảo PDF](docs/Phac_thao_giao_dien_video_toan_hoc.pdf),
gồm **Ý tưởng → Thiết kế cảnh → Xem bản dựng**, hỗ trợ desktop và điện thoại.
[Chi tiết giao diện và kiểm tra](docs/ui_redesign.md).

## Thiết kế cảnh bằng Manim

AI lập JSON thiết kế; bộ mã Manim cố định đọc đối tượng và timeline để dựng
preview tại máy. Preview không gọi AI viết/sửa Python và không dùng lượt request
AI. Có thể phát/tua video, tải mã và log rồi duyệt. Bản dựng không tiếng dùng
lại đúng video đã duyệt.

Thiết kế đã lưu hoặc phản hồi JSON hợp lệ trong cache có thể dựng tiếp ngay cả
khi job cũ đã hết lượt gọi AI. Lỗi render được lưu log; chỉ render lại khi yêu
cầu tiếp tục. Việc sửa mã clip theo góp ý vẫn có luồng agent riêng.

[Luồng preview, môi trường, lưu file và kiểm thử](docs/manim_agent.md).

## Bắt đầu từ ý tưởng trên giao diện

```bash
conda activate video
python main.py serve-review --project-dir data/projects/video_moi --port 8765
```

Thư mục mới được khởi tạo khi mở máy chủ; bạn không cần tạo dự án bằng CLI trước.
Nhập chủ đề, mục tiêu, người xem, khoảng thời lượng trong 30–60 giây, phong cách
và định dạng video tại `http://127.0.0.1:8765/#idea`. **Lưu bản nháp** lưu vào
dự án. **Tạo kịch bản từ ý tưởng** gửi các trường này làm đầu vào tạo nội dung
qua OpenRouter, dùng cấu hình `.env` hiện có.

Đọc kịch bản, góp ý hoặc nhập thời gian đọc thử rồi duyệt. Phần mềm tiếp tục tạo
thiết kế cảnh; sau khi duyệt đủ thiết kế sẽ dựng clip. Phần giọng đọc và video
cuối vẫn dùng cấu hình và các bước duyệt C7. Chọn video dọc áp dụng từ thiết kế
đến bản xuất 1080 × 1920. Tệp tham khảo hiện chỉ lưu tên, chưa được dùng làm nguồn.

Đổi ý tưởng rồi tạo lại bắt đầu lượt mới trong cùng dự án, giữ file và quyết định
cũ trong lịch sử. Các cảnh cũ không được dùng cho lượt mới; ngân sách cảnh và âm
thanh đã sử dụng vẫn được giữ. Mở trang hoặc lưu nháp không tự gọi AI.

## Lịch sử kịch bản lưu trên máy

Mở **Lịch sử kịch bản** ở thanh điều hướng hoặc truy cập
`http://127.0.0.1:8765/#history`. Danh sách hiển thị tất cả phiên bản đã tạo,
thời gian tạo xong theo giờ Việt Nam (GMT+7), trạng thái duyệt và bản đang sử dụng.
Có thể tìm theo tên/phiên bản, xem toàn bộ nội dung và tải `.md` hoặc dữ liệu
`.json` của bản AI. Bản lưu cũ chỉ để đọc; muốn duyệt/góp ý hãy mở bản hiện tại.

Lịch sử đọc trực tiếp từ `project.json` và `reviews.json` trong thư mục dự án.
Nội dung nằm trong `scripts/script_vNNN.md`, dữ liệu và nguồn của mỗi lượt AI
nằm trong `generation/<job_id>/`. Không phụ thuộc bộ nhớ trình duyệt:
đóng trình duyệt hay khởi động lại máy chủ với cùng `--project-dir` vẫn giữ lịch sử.
Các bản đã có trước khi cập nhật cũng được hiển thị với thời gian gốc.

Trang Ý tưởng mặc định thu gọn kịch bản cũ. Chọn **Mở kịch bản hiện tại** để xem
và duyệt. **Ý tưởng mới** mở biểu mẫu trống. **Tạo kịch bản mới từ ý tưởng**
luôn tạo một phiên bản riêng, kể cả khi giữ nguyên chủ đề; file và quyết định
cũ được giữ nguyên. **Thử lại / tiếp tục tạo kịch bản** tiếp tục lượt bị gián đoạn.
Mở lịch sử, tải bản lưu, mở trang và lưu nháp đều không gọi AI.

API chỉ đọc: `GET /api/script-history` và
`GET /api/script-history?version=v001`. Chỉ đọc phiên bản đã đăng ký trong dự án.
`/media` cho phép tải thêm `script.json` đã đăng ký, không mở JSON tùy ý.

Kiểm tra bổ sung (dùng AI giả):
`python -m unittest tests.test_script_history -v` và
`python tests/browser_script_history_smoke.py /đường/dẫn/python-của-môi-trường-video`.
Ảnh kiểm tra giao diện nằm trong `data/history_validation/`.

## Checkpoint 7 — Âm thanh và video hoàn chỉnh

Đã có video thiết kế Manim không tiếng, lời đọc Microsoft Edge (mặc định Hoài Mỹ),
căn nhịp theo cụm âm thanh, phụ đề và bản dựng 1080p/30 FPS. Duyệt từng cảnh
có tiếng rồi mới ghép và duyệt toàn bộ video. C7 có trần riêng 0,50 USD;
đơn giá FPT/tỷ giá chỉ áp dụng nếu chủ động chọn nguồn FPT. Microsoft Edge không yêu cầu API key, đơn giá hoặc tỷ giá.

```bash
python main.py av --project-dir DU_AN --scene-id scene_1
```

CLI C3–C6 vẫn tương thích. [Hướng dẫn C7, ngân sách và nghiệm thu](docs/checkpoint_7.md).

## Checkpoint 6 — Trang duyệt cảnh cục bộ

```bash
conda activate video
python main.py serve-review --project-dir data/projects/checkpoint_4_live/dao_ham --port 8765
```

Mở `http://127.0.0.1:8765` để xem thiết kế/clip, góp ý và duyệt đúng phiên bản.
C6 hỗ trợ duyệt lại thiết kế, phạm vi nhiều cảnh và giữ lịch sử; dùng chung
ngân sách C5 0,50 USD. [Hướng dẫn C6](docs/checkpoint_6.md). CLI có `--summary`
để xem gọn; mở trang không tự duyệt dự án.

## Checkpoint 5 — Thiết kế và dựng cảnh

Đã triển khai agent thiết kế và render Manim trước khi duyệt, ảnh Wikimedia có hồ sơ
CC0/Public domain, sinh mã Manim và dựng clip. Dùng `run --ai` sau khi duyệt
kịch bản C4; thêm `--scene-id` để xử lý riêng một cảnh. Ngân sách C5 là
0,50 USD/dự án. Thiết kế và clip đều cần bạn duyệt; không tự tạo video cuối.

[Xem hướng dẫn và nghiệm thu C5](docs/checkpoint_5.md). Nghiệm thu live chỉ
chạy tiếp sau quyết định duyệt thực tế của bạn.

## Checkpoint 4 — Nghiên cứu và viết kịch bản bằng OpenRouter

Đã có bộ sinh/sửa kịch bản dùng `anthropic/claude-haiku-5.5`, lưu nguồn HTML thực
được đọc, bảng dẫn chứng và phiên bản kịch bản. Chỉ `run --ai` mới gọi dịch vụ;
chờ duyệt không gọi lại API. Trần mỗi job 0,50 USD, tối đa sáu request model.

Điền cấu hình vào file `.env` ở thư mục gốc repo:

```dotenv
OPENROUTER_API_KEY="key_của_bạn"
OPENROUTER_MODEL="anthropic/claude-haiku-5.5"
```

Máy chủ giao diện cũng tự nạp `.env` khi khởi tạo, kể cả khi được gọi trực tiếp
từ Python. Sau khi cập nhật mã app hoặc sửa `.env`, khởi động lại `serve-review`
để tiến trình dùng cấu hình mới. Nếu giao diện gặp máy chủ cũ chưa hỗ trợ các
lệnh mới, trang sẽ báo cần khởi động lại trước khi cho tạo kịch bản.

CLI tự nạp file này khi chạy `main.py`, kể cả từ thư mục khác; không cần
`source .env`. Cấu hình đã đặt trong môi trường được ưu tiên. File `.env` được bỏ
qua trong Git; không ghi key vào mã hoặc dữ liệu dự án video.

```bash
python main.py create --project-dir data/projects/my_ai_video \
  --topic "Vì sao có ngày và đêm?" --brief examples/brief.json
python main.py run --project-dir data/projects/my_ai_video --ai
python main.py status --project-dir data/projects/my_ai_video
```

Sau khi kiểm tra nguồn và thực sự đo thời gian đọc lời dẫn:

```bash
python main.py review --project-dir data/projects/my_ai_video \
  --stage script --version v001 --decision approve --read-seconds 45
```

Không tự duyệt hoặc tạo thiết kế cảnh. [Hướng dẫn API, chi phí, khôi phục và nghiệm
thu live](docs/checkpoint_4.md). Brief mẫu nằm trong [examples/brief.json](examples/brief.json).

Kịch bản mới được viết thành một đoạn lời đọc trước, sau đó qua lượt biên tập
toàn đoạn rồi mới chia cảnh và thêm hướng dẫn Manim. Biên tập rà lặp ý, câu nối,
hook/payoff, cách nói và số cần chuyển lên hình. Bộ kiểm tra chặn câu lặp nguyên
văn, câu đệm, cảnh quá hai câu và quá một số dài trong lời đọc; lặp nghĩa và độ
tự nhiên do lượt AI biên tập xử lý, vẫn cần người dùng nghe/đọc thử khi duyệt.
Thời lượng lấy nguyên khoảng nhập trên UI; không áp cố định 30–35 giây. Bản nháp
liền mạch lưu ở `generation/<job_id>/narration_draft.json`; tiếp tục job dùng lại
phản hồi đã lưu. Job dở trước nâng cấp tiếp tục luồng cũ để giữ lịch sử lượt gọi;
lượt tạo hoặc yêu cầu sửa phiên bản tiếp theo dùng luồng mới.

Kiểm thử C4 offline không cần key:

```bash
python -S -m unittest tests.test_content tests.test_openrouter tests.test_generation tests.test_narration -v
```

Nghiệm thu tính phí được bật riêng bằng `RUN_LIVE_AI_TESTS=1` và key trong môi
trường: `python -m unittest tests.test_live_content -v`. Khi chưa có key, kiểm thử
live bị bỏ qua; không coi kiểm thử giả là kết quả tạo kịch bản thật.

## Checkpoint 3 — Bộ điều phối và cơ chế duyệt

Đã triển khai luồng `script → scene_design → scene_clip → final_video → completed`.
Duyệt thiết kế và clip riêng từng cảnh, theo đúng phiên bản; lưu phản hồi và
khôi phục tiến độ sau gián đoạn. Không gọi API/model AI.

```bash
python main.py create --project-dir data/projects/my_video --topic "Chuyển động"
python main.py run --project-dir data/projects/my_video --mock
python main.py status --project-dir data/projects/my_video
python main.py review --project-dir data/projects/my_video \
  --stage script --version v001 --decision approve
```

`run --mock` chỉ dùng nội dung cố định và clip mẫu checkpoint 2; luôn dừng ở điểm
chờ duyệt. Không có `--mock`, hệ thống hướng dẫn đăng ký file bằng `submit`.
Các quyết định chỉ chuyển trạng thái, không tự tạo nội dung bước sau.

Kiểm thử checkpoint 3 bằng thư viện chuẩn, không cần Manim:

```bash
python -S -m unittest tests.test_workflow tests.test_review -v
```

- [Hướng dẫn CLI, API và quy tắc duyệt](docs/checkpoint_3.md).
- [Lịch sử duyệt mô phỏng](data/projects/checkpoint_3_demo/reviews.json).
- [Kết quả dự án mẫu](data/projects/checkpoint_3_demo/verification/acceptance.json)
  và [các lần gọi CLI](data/projects/checkpoint_3_demo/verification/cli_transcript.json).

## Checkpoint 2 — Xây công cụ dựng Manim cho agent

- **2.1 đã chốt:** giao diện đầu vào/đầu ra và cấu trúc thư mục dự án.
- **2.2–2.5 đã hoàn thành:** công cụ dựng Manim dùng chung, lưu phiên bản,
  trả lỗi rõ ràng và kiểm thử bằng hai cảnh khác nhau cùng các trường hợp lỗi.

Hợp đồng và quy tắc chi tiết: [docs/checkpoint_2.md](docs/checkpoint_2.md).
Các file bố cục cũ `src/scene_design/scenes.py`, `config.py` và dữ liệu `dao_ham` vẫn được giữ
để tham khảo; chúng không phải tiêu chí của checkpoint 2 mới.

### Dùng từ Python

Chạy trong môi trường có Manim, ví dụ `conda activate video`, từ thư mục repo:

```python
import json
from pathlib import Path
from src.scene_design.render import render_scene

project = Path("data/projects/render_demo")
config = json.loads((project / "render_config.json").read_text())
result = render_scene(
    project_dir=project,
    scene_id="scene_01",
    version="v002",
    scene_file="scenes/scene_01.py",  # Tính từ thư mục dự án.
    scene_name="MovingDot",
    render_config=config,
)
print(result)
```

### Dùng từ dòng lệnh

```bash
conda activate video
python -m src.scene_design.render \
  --project-dir data/projects/render_demo \
  --scene-id scene_01 \
  --version v002 \
  --scene-file scenes/scene_01.py \
  --scene-name MovingDot \
  --render-config data/projects/render_demo/render_config.json
```

Lệnh in một JSON ra stdout, trả exit code `0` khi thành công, `1` khi thất bại.
Dùng phiên bản mới nếu clip, log hoặc metadata cùng tên đã tồn tại.
Python đang gọi công cụ phải có Manim; công cụ dùng chính Python đó để dựng.

Mỗi lần dựng mới có ba tệp: `previews/<scene_id>_<version>.mp4` khi thành công,
`logs/<scene_id>_<version>.log` và `logs/<scene_id>_<version>.json`.
JSON ghi trạng thái, cấu hình thực tế, thời điểm dựng và thông số video.
Kết quả hàm vẫn giữ sáu khóa đã chốt ở 2.1; lỗi là chuỗi `MÃ_LỖI: nguyên nhân`,
ví dụ `PYTHON_SYNTAX_ERROR`, `SCENE_NOT_FOUND` hoặc `TIMEOUT`.

### Kiểm tra checkpoint 2

```bash
conda activate video
python -m unittest discover -s tests -v
```

Chạy trong môi trường có Manim để các kiểm tra dựng thật không bị bỏ qua.
Bộ kiểm tra xác nhận lưu hai phiên bản và hai dự án riêng, xử lý mã lỗi,
dừng tác vụ cùng tiến trình con khi timeout, và đọc được MP4 đúng thông số.

Bằng chứng nghiệm thu nằm trong `data/projects/checkpoint_2_validation/`:

- [Điểm chuyển động v001](data/projects/checkpoint_2_validation/previews/scene_01_v001.mp4)
  và [v002](data/projects/checkpoint_2_validation/previews/scene_01_v002.mp4).
- [Hình chữ nhật đổi kích thước](data/projects/checkpoint_2_validation/previews/scene_02_v001.mp4).
- [Kết quả các lần gọi](data/projects/checkpoint_2_validation/acceptance.json),
  kèm log và metadata của từng lần trong `logs/`.
- [Khung hình đầu, giữa và cuối](data/projects/checkpoint_2_validation/review/scene_samples.png).

Các cảnh mẫu chỉ dùng kiểm tra công cụ. Phần agent tự tạo cảnh và ghép video
thuộc các checkpoint sau.


Chạy thử một cảnh: `settings/scene_generation.json` đặt `trial_mode: true` để giao diện chỉ chạy một cảnh, một lượt storyboard → thiết kế → render → đánh giá rồi dừng; không tự sửa hay tự duyệt. Lệnh tương đương: `python main.py run --ai --scene-trial --scene-id scene_1 --project-dir <project>`. Log, từng phản hồi Codex và video (nếu render được) vẫn lưu trong thư mục job. Đặt `trial_mode: false` và khởi động lại máy chủ để trở lại chế độ đầy đủ. FPT chỉ dùng ở bước giọng đọc.


Trong ứng dụng, video nháp qua kiểm tra chuyển động và render được xem ngay trong Thiết kế cảnh và được ghép ở Xem bản dựng, kể cả khi đánh giá hình ảnh chưa đạt. Nhãn Chưa duyệt và kết quả kiểm tra vẫn hiển thị; bản nháp không thay thế kết quả chính thức hoặc tự duyệt. Nút Chạy lại cảnh này tạo job mới có mã lượt riêng, giữ nguyên phản hồi và log các lượt trước.


Dựng lại thiết kế đã lưu: nút cùng tên trong Thiết kế cảnh chạy tại máy, không gọi Codex hay FPT, giữ nguyên lời dẫn và toán học; mọi điều chỉnh vị trí nhãn được ghi trong local_layout_changes.json. Xem bản dựng có thể tải MP4, SRT và báo cáo kiểm tra.

Giọng FPT: tùy chọn Tạm bỏ đơn giá và tỷ giá chỉ bật khi người dùng chọn rõ. Khi bật, log ghi reserved_usd=null, cost_status=unknown; giao diện không hiển thị chi phí này là 0 USD. Mặc định vẫn yêu cầu đơn giá/tỷ giá. Các HTTP 429 được chờ theo Retry-After và thử lại có giới hạn, dùng cache cho đoạn đã hoàn tất; các request chưa rõ kết quả không tự gửi lại. Tài liệu FPT: https://docs.fpt.ai/docs/vi/speech/api/text-to-speech/ ; HTTP 429: https://www.rfc-editor.org/rfc/rfc6585.html#section-4 .


## Giọng Microsoft mặc định

Cài `pip install -r requirements.txt` trong môi trường chạy app. Trong Xem bản dựng → Cấu hình giọng đọc, mặc định Microsoft Edge và Nam Minh (nam tiếng Việt), sau đó Nghe thử một đoạn hoặc Thêm tiếng. Cần Internet vì đây là dịch vụ online; `edge-tts` không cần API key của người dùng và không yêu cầu khóa trong `.env`. Không tự chuyển sang FPT khi Microsoft lỗi. Âm thanh đã tạo được lưu, kiểm tra SHA-256 và dùng lại; đổi nguồn/giọng/tốc độ tạo cache riêng và cập nhật bản có tiếng.

CLI: `python main.py audio-config --provider edge --voice vi-VN-NamMinhNeural --project-dir DU_AN`. Edge ưu tiên `WordBoundary`; khi không nhận âm thanh, lần thử tiếp theo dùng `SentenceBoundary` với đúng giọng và lời dẫn đã chọn. Thử lại lỗi tạm thời tối đa ba lần (chờ 2 và 5 giây), giới hạn toàn bộ lượt tải ở 75 giây. Phân biệt lỗi không trả âm thanh, lỗi kết nối, giới hạn tần suất và cấu hình. FPT và các log cũ vẫn được giữ để đọc lịch sử. Tài liệu thư viện: https://github.com/rany2/edge-tts .

### Nhạc nền cho toàn bộ video

Thư viện dùng chung ở `music/` ngay trong gốc AI_Create_video. Năm bài Mixkit đã tải có danh mục `music/catalog.json` lưu tác giả, nguồn, giấy phép, thời lượng và SHA-256. Không cần khóa API; các tệp nhạc phát tại máy.

Trong Xem bản dựng → Nhạc nền, nghe thử rồi tự chọn một bài cho cả video, hoặc Không dùng nhạc. Âm lượng mặc định 10% (giới hạn 0–30%); Tự hạ nhạc khi có lời đọc mặc định bật. Bấm Lưu và phối nhạc để áp dụng. Chưa chọn bài là mặc định. Khi đổi bài/âm lượng, ứng dụng dùng lại bản ghép lời đọc và hình đã có.

Nhạc được chuẩn hóa, nối vòng bằng crossfade, fade đầu 1 giây/cuối 2 giây, giảm dưới lời đọc và giới hạn đỉnh. Bản xem trước và bản xuất dùng chung bộ phối. Tải âm thanh WAV lấy đúng bản phối; Tải lời đọc riêng lấy tiếng không nhạc. Các thông số phối và hash nhạc được lưu trong manifest/cache. Đổi nhạc chỉ làm hết hiệu lực bản video cuối, giữ các duyệt cảnh. Dự án đã hoàn tất chỉ được xem.

API: `/api/state` chứa `project.music` (config và tracks); POST `/api/music-settings` nhận `config: {track_id: string|null, volume: 0..30, ducking: boolean}` cùng token/Origin/request_id như các thao tác khác. `/music-media?id=...` chỉ phục vụ bài có ID trong danh mục và hỗ trợ Range.




### Manim V2 repair policy

New v2 scene jobs default to two repair rounds after the initial render (at most
three rendered candidates). Set SCENE_TRIAL_REPAIRS=0..3 in the process environment or .env
to override trial jobs. Zero retains the single-pass trial. Existing jobs resume
their recorded policy; use a new attempt to adopt the new policy. Invalid values
fail before AI requests. A scene-local quality/render failure does not stop the
remaining scenes in an automatic sequence. Uncertain provider requests and
budget/auth errors still stop execution.

Polyline vertices are preserved. IDs containing the token wave receive a
decorative sine fallback only when no non-collinear path is supplied; emitted
waves use five cycles and reflected/return/echo waves nine. These are illustrative
shapes, not a physical frequency calculation. Replay does not call AI or TTS and
does not mark drafts as visually approved.

V2.1 adds up to two pre-render layout/timing patches per visual round. These
patches are AI requests and retain the existing request/budget/uncertain-result
guards; they do not consume visual repair rounds. If the precheck still fails,
the job stops before rendering rather than spending the remaining visual rounds.
SCENE_TRIAL_REPAIRS=0 also disables inner patches. Intermediate candidates/checks
are saved under round_XX/pre_render_YY; approved evidence and old jobs stay intact.
New jobs use prompt version 6. Geometry prechecks include line/plot/curve/axis
intersections with text boxes and text/dot overlap, respecting overlap_with.
Coincident bound points on the same graph use its local derivative as a tangent.
