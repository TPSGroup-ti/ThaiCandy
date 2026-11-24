# -*- coding: utf-8 -*-
"""
🍬 THAICANDY — System Connection Hub
📦 Version: 1.0.0
👤 Creator: Thanwa Phupingbut | Rufio D'noto
🏷️ Purpose: ตรวจสอบ · ลงทะเบียน · จัดการระบบที่เชื่อมเข้ามาแบบอัตโนมัติ

✅ คุณสมบัติ:
   📥 ลงทะเบียนระบบที่ต้องการเชื่อมต่อ
   🔍 ตรวจสอบสถานะการเชื่อมต่ออัตโนมัติ
   🤖 จัดการ เปิด-ปิด ตรวจสุขภาพระบบอัตโนมัติ
   🔗 สร้างตัวเชื่อมต่อสำหรับนำไปติดตั้ง
   📊 ดูสถิติและประวัติการเชื่อมต่อ
   📖 API Docs พร้อมใช้งาน
"""

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field
from fastapi import FastAPI, HTTPException, Header, Body
from fastapi.middleware.cors import CORSMiddleware

# ==========================================
# 💾 DATA STORE
# ==========================================

class DataStore:
    def __init__(self, root: str = "thaicandy_data"):
        self.root = Path(root)
        self.root.mkdir(exist_ok=True)
        self.systems_path = self.root / "registered_systems.json"
        self.logs_path = self.root / "connection_logs.json"
        self.tokens_path = self.root / "access_tokens.json"
        self._init()

    def _init(self):
        for f in [self.systems_path, self.logs_path, self.tokens_path]:
            if not f.exists():
                self._write(f, {} if "token" in f.name else [])

    def _read(self, path: Path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write(self, path: Path, data):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # Systems
    def get_systems(self): return self._read(self.systems_path)
    def save_system(self, sys_id: str, data: dict):
        systems = self.get_systems()
        systems[sys_id] = data
        self._write(self.systems_path, systems)
    def delete_system(self, sys_id: str):
        systems = self.get_systems()
        if sys_id in systems: del systems[sys_id]
        self._write(self.systems_path, systems)

    # Logs
    def add_log(self, entry: dict):
        logs = self._read(self.logs_path)
        entry["timestamp"] = datetime.now().isoformat()
        logs.insert(0, entry)
        self._write(self.logs_path, logs)
    def get_logs(self, limit: int = 100):
        return self._read(self.logs_path)[:limit]

    # Tokens
    def get_tokens(self): return self._read(self.tokens_path)
    def save_token(self, token: str, sys_id: str):
        tokens = self.get_tokens()
        tokens[token] = {"sys_id": sys_id, "created_at": datetime.now().isoformat()}
        self._write(self.tokens_path, tokens)
    def validate_token(self, token: str) -> Optional[str]:
        tokens = self.get_tokens()
        return tokens.get(token, {}).get("sys_id")


db = DataStore()

# ==========================================
# 🍬 CORE SERVICE
# ==========================================

class ThaiCandyService:
    def register_system(self, name: str, endpoint_url: str, description: str = "") -> dict:
        """➕ ลงทะเบียนระบบใหม่ — รับ Token เชื่อมต่อทันที"""
        sys_id = f"tc_{uuid.uuid4().hex[:12]}"
        token = f"tct_{uuid.uuid4().hex}"
        
        system_data = {
            "id": sys_id,
            "name": name,
            "endpoint_url": endpoint_url,
            "description": description,
            "status": "active",
            "connected_at": datetime.now().isoformat(),
            "last_seen": None,
            "health": "unknown"
        }
        
        db.save_system(sys_id, system_data)
        db.save_token(token, sys_id)
        db.add_log({
            "action": "system_registered",
            "system": name,
            "sys_id": sys_id
        })
        
        return {
            "ok": True,
            "message": f"✅ ลงทะเบียนระบบ {name} สำเร็จ",
            "system": system_data,
            "connection_token": token,
            "install_hint": f"นำ token นี้ไปใช้กับ connector.py ที่ระบบปลายทาง"
        }

    def check_connection(self, sys_id: str) -> dict:
        """🔍 ตรวจสอบสถานะการเชื่อมต่อ"""
        systems = db.get_systems()
        if sys_id not in systems:
            return {"ok": False, "message": "❌ ไม่พบระบบนี้"}
        
        sys = systems[sys_id]
        sys["last_seen"] = datetime.now().isoformat()
        sys["health"] = "online"
        db.save_system(sys_id, sys)
        
        db.add_log({
            "action": "heartbeat",
            "system": sys["name"],
            "status": "received"
        })
        
        return {
            "ok": True,
            "system_name": sys["name"],
            "status": sys["status"],
            "health": "✅ ออนไลน์",
            "last_seen": sys["last_seen"]
        }

    def list_systems(self, status: str = None) -> dict:
        """📋 รายการระบบทั้งหมด"""
        systems = db.get_systems()
        result = list(systems.values())
        if status:
            result = [s for s in result if s["status"] == status]
        return {
            "total": len(result),
            "systems": result
        }

    def toggle_system(self, sys_id: str, enable: bool) -> dict:
        """🔌 เปิด/ปิด การเชื่อมต่อระบบ"""
        systems = db.get_systems()
        if sys_id not in systems:
            return {"ok": False, "message": "❌ ไม่พบระบบนี้"}
        
        new_status = "active" if enable else "disabled"
        systems[sys_id]["status"] = new_status
        db.save_system(sys_id, systems)
        
        db.add_log({
            "action": "system_toggled",
            "system": systems[sys_id]["name"],
            "new_status": new_status
        })
        
        return {
            "ok": True,
            "message": f"✅ เปิดใช้งานระบบ" if enable else "⏸️ ปิดใช้งานระบบ",
            "system": systems[sys_id]["name"],
            "status": new_status
        }

    def generate_connector_code(self, sys_id: str) -> dict:
        """📤 สร้างโค้ดตัวเชื่อมต่อสำหรับนำไปใช้ที่ระบบอื่น"""
        systems = db.get_systems()
        if sys_id not in systems:
            return {"ok": False, "message": "❌ ไม่พบระบบนี้"}
        
        sys = systems[sys_id]
        tokens = db.get_tokens()
        token = next((t for t, s in tokens.items() if s["sys_id"] == sys_id), None)
        
        code_template = f'''# -*- coding: utf-8 -*-
"""
🍬 THAICANDY — System Connector
เชื่อมต่อระบบ: {sys['name']}
สร้างเมื่อ: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
วางไฟล์นี้ไว้ที่ระบบปลายทาง แล้วรัน: python connector.py
"""

import requests
import time
import json
from datetime import datetime

CONFIG = {{
    "thaicandy_url": "http://127.0.0.1:8120",  # เปลี่ยนเป็น URL จริงของ ThaiCandy
    "system_id": "{sys_id}",
    "connection_token": "{token}",
    "system_name": "{sys['name']}",
    "heartbeat_interval": 60  # วินาที/ครั้ง
}}

def send_heartbeat():
    """ส่งสัญญาณบอกสถานะกลับไปยัง ThaiCandy"""
    try:
        r = requests.post(
            f"{{CONFIG['thaicandy_url']}}/api/heartbeat",
            headers={
                "Content-Type": "application/json",
                "X-System-ID": CONFIG["system_id"],
                "X-Connection-Token": CONFIG["connection_token"]
            },
            json={{"system_name": CONFIG["system_name"]}},
            timeout=10
        )
        if r.status_code == 200:
            print(f"✅ เชื่อมต่อสำเร็จ — {{datetime.now().strftime('%H:%M:%S')}}")
            return True
    except Exception as e:
        print(f"⚠️  ไม่สามารถติดต่อ ThaiCandy: {{e}}")
    return False

def send_data(endpoint: str, data: dict):
    """ส่งข้อมูลไปยัง ThaiCandy"""
    try:
        r = requests.post(
            f"{{CONFIG['thaicandy_url']}}/api/{{endpoint}}",
            headers={{
                "Content-Type": "application/json",
                "X-System-ID": CONFIG["system_id"],
                "X-Connection-Token": CONFIG["connection_token"]
            }},
            json=data,
            timeout=15
        )
        return r.json() if r.content else None
    except Exception as e:
        return {{"ok": False, "error": str(e)}}

if __name__ == "__main__":
    print(f"🍬 ThaiCandy Connector — {{CONFIG['system_name']}}")
    print("=" * 50)
    print(f"ระบบ ID: {{CONFIG['system_id']}}")
    print(f"ThaiCandy: {{CONFIG['thaicandy_url']}}")
    print("กำลังเชื่อมต่อ...")
    print()
    
    # ทดสอบเชื่อมต่อ
    send_heartbeat()
    
    # ส่งสัญญาณเป็นระยะๆ
    print("🔄 กำลังเฝ้าดูการเชื่อมต่อ... (กด Ctrl+C เพื่อหยุด)")
    try:
        while True:
            time.sleep(CONFIG["heartbeat_interval"])
            send_heartbeat()
    except KeyboardInterrupt:
        print("\\n⏹️  หยุดการเชื่อมต่อ")
'''
        return {
            "ok": True,
            "connector_code": code_template,
            "instructions": "บันทึกเป็น connector.py ที่ระบบปลายทาง → ติดตั้ง requests → รัน python connector.py"
        }


candy = ThaiCandyService()

# ==========================================
# 📋 DATA MODELS
# ==========================================

class RegisterSystem(BaseModel):
    name: str = Field(..., max_length=100)
    endpoint_url: str = Field(..., description="URL หรือที่อยู่ของระบบนี้")
    description: str = ""

class HeartbeatData(BaseModel):
    system_name: str = ""
    status: str = "online"
    message: str = ""

# ==========================================
# 🚀 FASTAPI APP
# ==========================================

app = FastAPI(
    title="🍬 ThaiCandy — System Connection Hub",
    description="ตรวจสอบ · ลงทะเบียน · จัดการระบบที่เชื่อมเข้ามาแบบอัตโนมัติ",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================================
# 🏠 HOME
# ==========================================

@app.get("/")
def home():
    return {
        "system": "🍬 THAICANDY — System Connection Hub",
        "version": "1.0.0",
        "status": "✅ ONLINE",
        "endpoints": {
            "ลงทะเบียนระบบ": "/systems/register",
            "รายการระบบ": "/systems/list",
            "ส่งสัญญาณเชื่อมต่อ": "/api/heartbeat",
            "สร้างตัวเชื่อมต่อ": "/systems/{sys_id}/connector",
            "เปิด-ปิดระบบ": "/systems/{sys_id}/toggle",
            "ประวัติการเชื่อมต่อ": "/logs",
            "สถิติ": "/stats",
            "เอกสาร": "/docs"
        },
        "creator": "Thanwa Phupingbut | Rufio D'noto"
    }

# ==========================================
# 🖥️ SYSTEM MANAGEMENT
# ==========================================

@app.post("/systems/register")
def register_system(sys: RegisterSystem):
    """➕ ลงทะเบียนระบบใหม่ รับ Token และโค้ดเชื่อมต่อทันที"""
    result = candy.register_system(sys.name, sys.endpoint_url, sys.description)
    return result

@app.get("/systems/list")
def list_systems(status: str = None):
    """📋 ดูรายการระบบทั้งหมด"""
    return candy.list_systems(status)

@app.get("/systems/{sys_id}/connector")
def get_connector(sys_id: str):
    """📤 ดาวน์โหลดโค้ดตัวเชื่อมต่อสำหรับระบบนี้"""
    return candy.generate_connector_code(sys_id)

@app.post("/systems/{sys_id}/toggle")
def toggle_system(sys_id: str, enable: bool = Body(True, embed=True)):
    """🔌 เปิดหรือปิดการเชื่อมต่อระบบ"""
    return candy.toggle_system(sys_id, enable)

@app.delete("/systems/{sys_id}")
def remove_system(sys_id: str):
    """🗑️ ลบระบบออกจากการจัดการ"""
    systems = db.get_systems()
    if sys_id not in systems:
        raise HTTPException(404, "ไม่พบระบบ")
    name = systems[sys_id]["name"]
    db.delete_system(sys_id)
    db.add_log({"action": "system_removed", "system": name})
    return {"ok": True, "message": f"ลบระบบ {name} แล้ว"}

# ==========================================
# 🔗 CONNECTION API — สำหรับระบบภายนอกเชื่อมเข้ามา
# ==========================================

@app.post("/api/heartbeat")
def heartbeat(
    data: HeartbeatData,
    x_system_id: str = Header(None, alias="X-System-ID"),
    x_connection_token: str = Header(None, alias="X-Connection-Token")
):
    """💓 ส่งสัญญาณบอกว่าระบบยังออนไลน์ — ระบบภายนอกเรียกนี้เป็นประจำ"""
    if not x_system_id or not x_connection_token:
        raise HTTPException(401, "ต้องระบุ X-System-ID และ X-Connection-Token")
    
    valid_sys_id = db.validate_token(x_connection_token)
    if not valid_sys_id or valid_sys_id != x_system_id:
        raise HTTPException(403, "Token ไม่ถูกต้อง")
    
    return candy.check_connection(x_system_id)

# ==========================================
# 📊 MONITORING
# ==========================================

@app.get("/logs")
def get_logs(limit: int = 50):
    """📋 ประวัติการเชื่อมต่อทั้งหมด"""
    return {"logs": db.get_logs(limit)}

@app.get("/stats")
def get_stats():
    """📊 ภาพรวมสถานะ"""
    systems = db.get_systems()
    logs = db.get_logs(200)
    online = len([s for s in systems.values() if s.get("health") == "online"])
    return {
        "total_systems": len(systems),
        "online": online,
        "offline": len(systems) - online,
        "active": len([s for s in systems.values() if s["status"] == "active"]),
        "recent_connections": len([l for l in logs if l.get("action") == "heartbeat"])
    }

# ==========================================
# 🚀 RUN SERVER
# ==========================================

if __name__ == "__main__":
    import uvicorn
    PORT = int(__import__("os").getenv("PORT", 8120))
    print(f"""
{'='*60}
🍬 THAICANDY — System Connection Hub v1.0.0
{'='*60}
✅ พร้อมใช้งาน — จัดการการเชื่อมต่อระบบอัตโนมัติ

🌐 หน้าหลัก:    http://127.0.0.1:{PORT}
📖 API Docs:    http://127.0.0.1:{PORT}/docs
📦 ข้อมูลที่:   ./thaicandy_data/

📥 วิธีใช้งาน:
   1️⃣ ลงทะเบียนระบบที่ /systems/register
   2️⃣ ดาวน์โหลดตัวเชื่อมต่อที่ /systems/{"{sys_id}"}/connector
   3️⃣ นำ connector.py ไปรันที่ระบบปลายทาง
   4️⃣ ดูสถานะการเชื่อมต่อได้ที่ /stats และ /systems/list

💎 Creator: Thanwa Phupingbut | Rufio D'noto
{'='*60}
""")
    uvicorn.run(app, host="0.0.0.0", port=PORT)

