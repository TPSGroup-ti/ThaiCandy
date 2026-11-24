# ThaiCandy External API Gateway

บริการ API กลางสำหรับรับ/อ่านข้อมูลจากระบบภายนอก แยกจาก Repository ต้นทางของ ThaiCandy และ Deploy เป็น Railway service แยกได้

## ความสามารถ

- `GET /api/v1/health` ตรวจสถานะบริการ (ไม่ต้องใช้ key)
- `POST /api/v1/ingest` รับ JSON จากระบบภายนอก (ต้องมี scope `data:write`)
- `GET /api/v1/records/{record_id}` อ่านได้เฉพาะข้อมูลของ `client_id` ที่ผูกกับ API key นั้น (ต้องมี scope `data:read`)
- `GET /api/v1/admin/quarantine` เจ้าของโครงการตรวจรายการที่ถูกกักกัน (ใช้ `X-Admin-Key`)
- API key ถูกเก็บในหน่วยความจำเป็น SHA-256 digest; รองรับ Idempotency-Key, จำกัด payload 256 KiB, rate limit เบื้องต้น 120 requests/client/minute และ redact ค่าที่อยู่ในฟิลด์ลักษณะ credential ก่อนบันทึก
- Swagger UI: `/docs`; OpenAPI: `/openapi.json`

ข้อมูลที่รับเข้ามาเป็นข้อมูลเท่านั้น ไม่มีการ execute คำสั่ง, SQL, shell หรือโค้ดจาก payload

## ติดตั้งในเครื่อง

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux:
source .venv/bin/activate
pip install -r requirements.txt
```

สร้าง secret ด้วย Python (ทำในเครื่องที่เชื่อถือได้):

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

กำหนด Environment Variables ตาม `.env.example` (อย่า commit ค่า secret จริง) แล้วรัน:

```bash
uvicorn thaicandy_gateway:app --host 0.0.0.0 --port 8080
```

## Deploy บน Railway

1. สร้าง Railway service ใหม่จากโฟลเดอร์นี้ หรือเชื่อม repository สำหรับ API โดยเฉพาะ
2. ตั้ง Variables: `THAICANDY_ADMIN_KEY`, `THAICANDY_API_KEYS`, `THAICANDY_GATEWAY_DB=/data/thaicandy_gateway.sqlite3`
3. เพิ่ม Railway Volume และ mount ที่ `/data` เพื่อให้ SQLite ไม่หายเมื่อ container ถูกสร้างใหม่
4. Deploy ด้วย Dockerfile; Railway ใช้ `PORT` อัตโนมัติ
5. เปิด public domain/HTTPS ให้ service แล้วตรวจ `https://<โดเมน>/api/v1/health` และ `/docs`
6. ส่ง URL และ API key ให้ระบบภายนอกผ่านช่องทางลับเท่านั้น ห้ามใส่ key ใน frontend/browser หรือ URL query string

**สำคัญ:** โค้ดนี้ยังไม่ใช่บริการที่ Deploy แล้ว การสร้างไฟล์ไม่ทำให้เกิด public URL โดยอัตโนมัติ ต้องตั้งค่า Railway Variables, Volume และ Deploy ก่อน

## ตัวอย่างเชื่อมต่อจากระบบภายนอก

ตั้งค่าตัวแปร `THAICANDY_API_URL` และ `THAICANDY_API_KEY` ใน backend ของระบบภายนอก แล้วเรียก:

```bash
curl -X POST "$THAICANDY_API_URL/api/v1/ingest" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $THAICANDY_API_KEY" \
  -H "Idempotency-Key: erp-invoice-2026-0001" \
  -d '{"source":"erp","data_type":"finance.invoice","payload":{"invoice_no":"INV-0001","amount":1250}}'
```

อ่าน record ที่ระบบเดียวกันส่งเข้ามา:

```bash
curl "$THAICANDY_API_URL/api/v1/records/<record_id>" \
  -H "X-API-Key: $THAICANDY_API_KEY"
```

ตัวอย่าง Python:

```python
import os, requests

base = os.environ["THAICANDY_API_URL"].rstrip("/")
headers = {"X-API-Key": os.environ["THAICANDY_API_KEY"]}
r = requests.post(
    f"{base}/api/v1/ingest",
    headers={**headers, "Idempotency-Key": "crm-contact-0001"},
    json={"source": "crm", "data_type": "customer.contact", "payload": {"name": "Example", "email": "person@example.org"}},
    timeout=20,
)
r.raise_for_status()
print(r.json())
```

## Data types ที่อนุญาต

`customer.profile`, `customer.contact`, `content.document`, `content.media_metadata`, `finance.invoice`, `finance.transaction`, `inventory.item`, `inventory.movement`, `operations.event`, `analytics.event`, `project.record`, `integration.webhook`, `custom.record`

## ทดสอบ

```bash
python -m pytest -q
```

## ขอบเขตและความปลอดภัยก่อนใช้งานจริง

- ใช้ HTTPS เท่านั้น; API key ใช้ใน server-to-server backend เท่านั้น
- ตั้ง key แยกตามระบบและให้ scopes เท่าที่จำเป็น; หมุน key เมื่อสงสัยว่ารั่วไหล
- SQLite + rate limit ใน process เหมาะกับการเริ่มต้น single instance; งาน production หลาย instance ควรย้ายไป PostgreSQL และใช้ distributed rate limiter
- ใช้ volume ที่ persistent, backup, retention/deletion policy และ encryption-at-rest ตามความเหมาะสม
- การตรวจ payload เป็นเพียงการคัดกรอง ไม่ใช่ระบบ DLP/antivirus; หลีกเลี่ยงข้อมูลส่วนบุคคล/ข้อมูลอ่อนไหวที่ไม่จำเป็น และกำหนดฐานการประมวลผลตาม PDPA
- Admin key ใช้กับ endpoint quarantine เท่านั้น; ห้ามส่ง admin key ให้ระบบภายนอก
