# -*- coding: utf-8 -*-
"""
Authentication 路由
處理信箱驗證碼寄發、驗證，以及訪客 Token 發放
"""
import uuid
import secrets
import string
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from backend.database import get_db, User
from backend.auth_utils import create_access_token, send_verification_email

router = APIRouter(prefix="/auth", tags=["auth"])

# ── 請求模型 ──
class SendCodeRequest(BaseModel):
    email: EmailStr

class VerifyCodeRequest(BaseModel):
    email: EmailStr
    code: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str
    role: str
    owner_id: str

@router.post("/send-code")
def send_code(req: SendCodeRequest, db: Session = Depends(get_db)):
    # 產生 6 位數隨機數字
    code = "".join(secrets.choice(string.digits) for _ in range(6))
    expire_time = datetime.now(timezone.utc) + timedelta(minutes=5)

    user = db.query(User).filter(User.email == req.email).first()
    if not user:
        user = User(email=req.email, verification_code=code, code_expires_at=expire_time)
        db.add(user)
    else:
        user.verification_code = code
        user.code_expires_at = expire_time
    
    db.commit()

    try:
        send_verification_email(req.email, code)
    except Exception as e:
        raise HTTPException(status_code=500, detail="寄發信件失敗，請檢查系統 SMTP 設定")
    
    return {"message": "驗證碼已寄出"}

@router.post("/verify-code", response_model=TokenResponse)
def verify_code(req: VerifyCodeRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == req.email).first()
    if not user:
        raise HTTPException(status_code=400, detail="信箱不存在或尚未寄發驗證碼")
    
    # 判斷過期或錯誤
    if not user.verification_code or not user.code_expires_at:
        raise HTTPException(status_code=400, detail="無效的操作")

    # 因為 datetime 從 sqlite 讀出來可能是 naive, 若沒時區則補上 utc
    expires_at = user.code_expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if datetime.now(timezone.utc) > expires_at:
        raise HTTPException(status_code=400, detail="驗證碼已過期")
    
    if user.verification_code != req.code:
        raise HTTPException(status_code=400, detail="驗證碼錯誤")
    
    # 驗證成功，清除資料庫中的驗證碼
    user.verification_code = None
    user.code_expires_at = None
    db.commit()

    # 發放 Token
    access_token = create_access_token(data={"owner_id": user.email, "role": "user"})
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "role": "user",
        "owner_id": user.email
    }


@router.post("/guest", response_model=TokenResponse)
def guest_login():
    """發放訪客 Token"""
    guest_id = f"guest_{uuid.uuid4().hex}"
    access_token = create_access_token(data={"owner_id": guest_id, "role": "guest"})
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "role": "guest",
        "owner_id": guest_id
    }
