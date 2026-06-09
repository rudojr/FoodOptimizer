# FoodOptimizer

Hệ thống tự động xây dựng thực đơn tuần cho trường học/nhà trẻ sử dụng **OR-Tools CP-SAT** (constraint programming) kết hợp **FastAPI**.

## Tính năng

- Tự động tạo thực đơn 5 ngày/tuần (Thứ 2 – Thứ 6) với 6 suất/ngày: 2 món mặn, rau, canh, cơm, quà chiều
- Áp dụng đầy đủ quy định dinh dưỡng và phối hợp món ăn
- API repair để thay thế từng món khi cần điều chỉnh thủ công
- Validate ràng buộc cho thực đơn bất kỳ

## Cấu trúc

```
FoodOptimizer/
├── backend/
│   ├── main.py          # FastAPI app, endpoints
│   ├── optimizer.py     # CP-SAT solver — xây dựng thực đơn tối ưu
│   ├── data_loader.py   # Đọc raw.xlsx, phân loại và chấm điểm món ăn
│   ├── local_repair.py  # Sửa từng slot vi phạm ràng buộc
│   ├── models.py        # Pydantic schemas
│   └── requirements.txt
├── frontend/
│   ├── index.html
│   ├── app.js
│   └── style.css
└── raw.xlsx             # Dữ liệu món ăn (không commit)
```

## Cài đặt

```bash
cd backend
python3 -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## Chạy

```bash
cd backend
source venv/bin/activate
python main.py
```

Truy cập:
- **App**: http://127.0.0.1:8000
- **API Docs**: http://127.0.0.1:8000/api/docs

## Dữ liệu đầu vào

Đặt file `raw.xlsx` tại thư mục gốc (cùng cấp với `backend/`). File cần có các cột:

| Cột | Mô tả |
|-----|-------|
| A | Mã món (code) |
| B | Tên món |
| C | Mã nguyên liệu |

Mỗi món có thể có nhiều dòng (nhiều nguyên liệu). `data_loader` tự phân loại thành các nhóm M / R / C / CO / Q.

## API

| Method | Endpoint | Mô tả |
|--------|----------|-------|
| `POST` | `/api/optimize` | Tạo thực đơn tuần mới |
| `POST` | `/api/repair` | Gợi ý thay thế 1 slot |
| `POST` | `/api/auto-repair` | Tự động sửa các vi phạm |
| `POST` | `/api/validate` | Kiểm tra ràng buộc thực đơn |
| `GET`  | `/api/dishes` | Danh sách món đã load |
| `GET`  | `/health` | Trạng thái server |

### Tạo thực đơn

```bash
curl -X POST http://127.0.0.1:8000/api/optimize \
  -H "Content-Type: application/json" \
  -d '{"timeout_seconds": 8, "allow_dish_repeat": false}'
```

## Ràng buộc thực đơn

### Nguyên liệu & phối hợp
- Cá basa chỉ dùng cho món viên, không dùng chiên bột
- Không kết hợp cá/tôm với trứng trong cùng bữa
- Bữa có cá/tôm không kết hợp sữa trong quà chiều
- Rau củ quả → canh phải thuộc nhóm rau ăn lá
- Không dùng rau muống

### Tần suất
- Tối đa 1 món viên / tuần
- Tôm tối đa 1 bữa / tuần
- Thịt bò tối đa 1 bữa / tuần
- Cơm rang tối đa 1 lần / tuần
- Cơm gà tối đa 1 lần / tuần

### Chế biến
- Không bố trí 2 món chiên trong cùng một ngày

## Scoring

Mỗi món có `preference_score` được tính trong `data_loader.py`:

| Score | Loại |
|-------|------|
| `+10` | Món ưa thích (xá xíu, viên, nem lụi, xúc xích…) |
| `+1`  | Trung tính |
| `-5`  | Hạn chế (cải cúc, rang nấm hương, gà sốt chua ngọt…) |

Solver tối đa hóa tổng score của cả thực đơn tuần.
