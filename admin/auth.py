import configparser
import os
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

security = HTTPBasic()
ADMIN_CFG = "configs/admin.cfg"


def init_admin_cfg():
    if not os.path.exists(ADMIN_CFG):
        config = configparser.ConfigParser()
        config["admin"] = {"username": "admin", "password": "changeme"}
        with open(ADMIN_CFG, "w", encoding="utf-8") as f:
            config.write(f)


def get_current_user(credentials: HTTPBasicCredentials = Depends(security)):
    init_admin_cfg()
    config = configparser.ConfigParser()
    config.read(ADMIN_CFG, encoding="utf-8")

    correct_username = config["admin"]["username"]
    correct_password = config["admin"]["password"]

    if credentials.username != correct_username or credentials.password != correct_password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username