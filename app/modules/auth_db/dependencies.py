from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
from app.core.database import SessionLocal
from app.modules.auth_db import models
from app.core.security import SECRET_KEY, ALGORITHM

oauth2_scheme = OAuth2PasswordBearer(tokenUrl='modules/auth_db/login')

def get_user_from_token(token: str):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get('sub')
        if username is None:
            raise HTTPException(status_code=401, detail='Token inválido')
    except JWTError:
        raise HTTPException(status_code=401, detail='Token inválido')
    
    db = SessionLocal()
    user = db.query(models.User).filter(models.User.username == username).first()
    db.close()
    if user is None:
        raise HTTPException(status_code=401, detail='Usuario no encontrado')
    if user.is_active is False:
        raise HTTPException(status_code=401, detail='Usuario inactivo')
    return user


def get_current_user(token: str = Depends(oauth2_scheme)):
    return get_user_from_token(token)
