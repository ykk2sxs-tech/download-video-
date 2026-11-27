@echo off
chcp 65001 >nul
title Video Downloader
cd /d "%~dp0"

set "PY="
where py >nul 2>nul
if not errorlevel 1 (
  set "PY=py -3"
) else (
  where python >nul 2>nul
  if not errorlevel 1 set "PY=python"
)
if not defined PY (
  echo ไม่พบ Python - กรุณาติดตั้งจาก https://www.python.org/downloads/
  echo ตอนติดตั้งให้ติ๊ก "Add python.exe to PATH" ด้วย แล้วดับเบิลคลิกไฟล์นี้อีกครั้ง
  pause
  exit /b 1
)

if not exist "venv\Scripts\python.exe" (
  echo ติดตั้งครั้งแรก กำลังสร้างสภาพแวดล้อม Python...
  %PY% -m venv venv
  if errorlevel 1 (
    echo สร้าง venv ไม่สำเร็จ
    pause
    exit /b 1
  )
)

echo กำลังตรวจสอบ/อัปเดตโปรแกรม...
"venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q --upgrade -r requirements.txt
if errorlevel 1 echo อัปเดตไม่สำเร็จ (อาจไม่มีอินเทอร์เน็ต) - ใช้เวอร์ชันเดิมต่อ

"venv\Scripts\python.exe" app.py %*
pause
