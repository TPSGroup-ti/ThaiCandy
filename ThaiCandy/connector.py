# -*- coding: utf-8 -*-
"""
🍬 THAICANDY — System Connector
เชื่อมต่อระบบของคุณเข้ากับ ThaiCandy Central
สร้างจากระบบกลาง — นำไปรันที่ไหนก็ได้
"""

import requests
import time
import json
from datetime import datetime

# ⚙️ ตั้งค่าที่ได้จากหน้า ThaiCandy /systems/register
CONFIG = {
    "thaicandy_url": "http://127.0.0.1:8120",  "https://www.facebook.com/share/1BjhVjiAfY/", # เปลี่ยนเป็น URL จริงของคุณ
    "system_id": "tc_xxxxxxxxxxxx",
    "connection_token": "tct_xxxxxxxxxxxxxxxxxxxxxxxx",
    "system_name": "ระบบของฉัน",
    "heartbeat_interval": 60  # ส่งสัญญาณทุกกี่วินาที
}

def send_heartbeat():
    """💓 ส่งสัญญาณบอกสถานะกลับไปยัง ThaiCandy"""
    try:
        r = requests.post(
            f"{CONFIG['thaicandy_url']}/api/heartbeat",
            headers={
                "Content-Type": "application/json",
                "X-System-ID": CONFIG["system_id"],
                "X-Connection-Token": CONFIG["connection_token"]
            },
            json={
                "system_name": CONFIG["system_name"],
                "status": "online",
                "message": "ทำงานปกติ"
            },
            timeout=10
        )
        if r.status_code == 200:
            print(f"✅ เชื่อมต่อสำเร็จ — {datetime.now().strftime('%H:%M:%S')}")
            return True
        else:
            print(f"⚠️  ตอบกลับ: รหัส {r.status_code}")
    except Exception as e:
        print(f"❌ ไม่สามารถติดต่อ ThaiCandy: {e}")
    return False

def send_custom_data(endpoint: str, data: dict):
    """📤 ส่งข้อมูลกำหนดเองไปยัง ThaiCandy"""
    try:
        r = requests.post(
            f"{CONFIG['thaicandy_url']}/{endpoint.lstrip('/')}",
            headers={
                "Content-Type": "application/json",
                "X-System-ID": CONFIG["system_id"],
                "X-Connection-Token": CONFIG["connection_token"]
            },
            json=data,
            timeout=15
        )
        return r.json() if r.content else {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def check_connection():
    """🔍 ทดสอบการเชื่อมต่อครั้งเดียว"""
    print(f"🍬 กำลังเชื่อมต่อ: {CONFIG['system_name']}")
    print(f"🌐 ที่อยู่: {CONFIG['thaicandy_url']}")
    print(f"🆔 System ID: {CONFIG['system_id']}")
    print("-" * 50)
    return send_heartbeat()

if __name__ == "__main__":
    check_connection()
    print()
    print("🔄 กำลังเฝ้าดูการเชื่อมต่อ... (กด Ctrl+C เพื่อหยุด)")
    print()
    
    try:
        while True:
            time.sleep(CONFIG["heartbeat_interval"])
            send_heartbeat()
    except KeyboardInterrupt:
        print("\n⏹️  หยุดการเชื่อมต่อ — ออนไลน์ล่าสุดเมื่อ:", 
              datetime.now().strftime('%Y-%m-%d %H:%M:%S'))

