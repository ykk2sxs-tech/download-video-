#!/usr/bin/env bash
# เปิดแอปดาวน์โหลดวิดีโอ (macOS / Linux) — ดับเบิลคลิก หรือรัน ./start.sh
set -e
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "ไม่พบ Python 3 — กรุณาติดตั้งจาก https://www.python.org/downloads/ แล้วลองใหม่"
  exit 1
fi

if [ ! -x venv/bin/python ]; then
  echo "ติดตั้งครั้งแรก กำลังสร้างสภาพแวดล้อม Python…"
  python3 -m venv venv
fi

echo "กำลังตรวจสอบ/อัปเดตโปรแกรม…"
venv/bin/python -m pip install --disable-pip-version-check -q --upgrade -r requirements.txt \
  || echo "อัปเดตไม่สำเร็จ (อาจไม่มีอินเทอร์เน็ต) — ใช้เวอร์ชันเดิมต่อ"

exec venv/bin/python app.py "$@"
