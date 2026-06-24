from sqlalchemy import create_engine
from urllib.parse import quote_plus
import streamlit as st

# 读取密钥
db_cfg = st.secrets["database"]
user = db_cfg["user"]
pwd = quote_plus(db_cfg["password"])
host = db_cfg["host"]
port = db_cfg["port"]
db_name = db_cfg["database"]
ca_file = db_cfg["ca_path"]


# 拼接带SSL参数的连接串
engine_str = (
    f"mysql+pymysql://{user}:{pwd}@{host}:{port}/{db_name}"
    f"?charset=utf8mb4&ssl_ca={ca_file}"
)

engine = create_engine(
    engine_str,
    pool_pre_ping=True,
    pool_recycle=3600
)