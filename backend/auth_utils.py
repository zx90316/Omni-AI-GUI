import os
import smtplib
from email.message import EmailMessage
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict

import jwt
from fastapi import Request, HTTPException, Security, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from dotenv import load_dotenv

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY", "omni_ai_secret_key_change_me_in_prod")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 30 # Token expires in 30 days
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = os.getenv("SMTP_PORT", "465")
SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL")


security = HTTPBearer(auto_error=False)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def verify_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

def get_current_user(request: Request, credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    token = None
    if credentials:
        token = credentials.credentials
    elif "token" in request.query_params:
        token = request.query_params["token"]
    
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
        
    # Should return a dict containing {"owner_id": <id>, "role": "user" | "guest"}
    return verify_token(token)

def send_verification_email(to_email: str, code: str):
    msg = EmailMessage()
    msg.set_content(f"您的 Omni AI 登入驗證碼為：{code}\n\n驗證碼 5 分鐘內有效。請勿將此驗證碼告訴他人。")
    msg["Subject"] = "Omni AI 登入驗證碼"
    
    # 決定寄件者顯示信箱
    if SMTP_FROM_EMAIL:
        msg["From"] = SMTP_FROM_EMAIL
    elif SMTP_USER:
        msg["From"] = SMTP_USER
    else:
        msg["From"] = "noreply@omni-ai.local"
        
    msg["To"] = to_email

    try:
        port = int(SMTP_PORT)
        if port == 465:
            # 針對 465 port (SSL)
            with smtplib.SMTP_SSL(SMTP_HOST, port) as server:
                if SMTP_USER and SMTP_PASSWORD:
                    server.login(SMTP_USER, SMTP_PASSWORD)
                server.send_message(msg)
        else:
            # 針對其他 port 通常使用 STARTTLS (例如 587, 25 等)
            with smtplib.SMTP(SMTP_HOST, port) as server:
                if port != 25:
                    try:
                        server.starttls()
                    except Exception:
                        pass
                
                if SMTP_USER and SMTP_PASSWORD:
                    server.login(SMTP_USER, SMTP_PASSWORD)
                
                server.send_message(msg)
    except Exception as e:
        import traceback
        error_detail = f"Failed to send email: {e}\n{traceback.format_exc()}"
        print(error_detail)
        raise ValueError(f"寄送郵件失敗: {e}")
