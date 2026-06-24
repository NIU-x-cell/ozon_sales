import pandas as pd
import numpy as np
from sqlalchemy import text
from datetime import datetime
from config import engine
import warnings
warnings.filterwarnings("ignore")

# ========== SQL单引号转义函数 ==========
def escape_sql_str(val):
    if pd.isna(val):
        return ""
    return str(val).replace("'", "''")

# 1 读取清洗Excel
def load_clean_excel():
    df_raw = pd.read_excel("./跟卖处理表/跟卖处理表.xlsx")
    rename_map = {
        "创建日期": "create_time",
        "创建人": "creator",
        "跟卖店铺id": "shop_id",
        "跟卖sku": "sku",
        "店铺名称": "shop_name",
        "运营": "operator",
        "组长": "team_leader",
        "负责人": "manager",
        "处理情况": "deal_status",
        "备注情况": "remark",
        "处理异议图片": "img1",
        "处理异议图片1": "img2",
        "移交管理处理": "transfer_type",
        "更新时间": "update_time",
        "处理时效": "deal_hour"
    }
    df_raw.rename(columns=rename_map, inplace=True)
    if "@负责人" in df_raw.columns:
        df_raw.drop(columns=["@负责人"], inplace=True)
    # 不存在img2则创建空列
    if "img2" not in df_raw.columns:
        df_raw["img2"] = ""
    # 清理空行、重复行
    df = df_raw.dropna(how="all")
    df = df.drop_duplicates()
    # 时间转换
    df["create_time"] = pd.to_datetime(df["create_time"], errors="coerce")
    df["update_time"] = pd.to_datetime(df["create_time"], errors="coerce")
    df["create_time"] = df["create_time"].where(df["create_time"].notna(), None)
    df["update_time"] = df["update_time"].where(df["update_time"].notna(), None)
    df["create_date"] = df["create_time"].dt.date
    df["weekday"] = df["create_time"].dt.day_name()
    df["hour"] = df["create_time"].dt.hour

    # 计算真实处理时效
    def calc_hour(row):
        if pd.isna(row["create_time"]) or pd.isna(row["update_time"]):
            return np.nan
        delta = row["update_time"] - row["create_time"]
        return round(delta.total_seconds() / 3600, 2)
    df["real_deal_hour"] = df.apply(calc_hour, axis=1)

    # 工单层级
    def get_level(row):
        if pd.notna(row["operator"]):
            return "运营层工单"
        elif pd.notna(row["team_leader"]):
            return "组长层工单"
        else:
            return "负责人层工单"
    df["ticket_level"] = df.apply(get_level, axis=1)

    # 异常标记
    df["is_overtime"] = ((df["deal_status"] == "未完成") & (df["real_deal_hour"] > 72)).astype(int)
    df["is_transfer"] = df["transfer_type"].notna().astype(int)
    df["invalid_sku"] = df["sku"].isna().astype(int)
    sku_count = df["sku"].value_counts()
    repeat_sku = sku_count[sku_count >= 3].index
    df["repeat_sku"] = df["sku"].isin(repeat_sku).astype(int)
    df["is_delete"] = 0
    return df

# 2 查询全量数据库数据
def get_db_all():
    sql = "SELECT * FROM sale_ticket"
    df_db = pd.read_sql(sql, engine)
    return df_db

# 3 同步主逻辑
def sync_excel_to_mysql():
    df_excel = load_clean_excel()
    # 数据库标准字段
    table_cols = [
        "create_time", "creator", "shop_id", "sku", "shop_name", "operator", "team_leader", "manager",
        "deal_status", "remark", "img1", "img2", "transfer_type", "update_time", "deal_hour",
        "real_deal_hour", "ticket_level", "is_overtime", "is_transfer", "invalid_sku", "repeat_sku",
        "create_date", "weekday", "hour", "is_delete"
    ]
    df_excel = df_excel[table_cols]
    df_db = get_db_all()

    # ========== 新增：判断数据库为空，直接全量新增 ==========
    if df_db.empty:
        df_excel.to_sql(name="sale_ticket", con=engine, if_exists="append", index=False)
        print(f"数据库无历史数据，全量新增 {len(df_excel)} 条工单入库")
        print("===== 数据同步完成 ====")
        return

    # 构造临时唯一标识（仅内存使用，不入库）
    df_excel["uk_temp"] = df_excel["create_time"].astype(str) + "_" + df_excel["creator"] + "_" + df_excel["sku"].astype(str)
    df_db["uk_temp"] = df_db["create_time"].astype(str) + "_" + df_db["creator"] + "_" + df_db["sku"].astype(str)

    excel_keys = set(df_excel["uk_temp"].unique())
    db_keys = set(df_db["uk_temp"].unique())
    delete_keys = db_keys - excel_keys
    add_df = df_excel[~df_excel["uk_temp"].isin(db_keys)].copy()
    update_df = df_excel[df_excel["uk_temp"].isin(db_keys)].copy()

    # 1 新增数据
    if len(add_df) > 0:
        add_df.drop(columns="uk_temp", inplace=True)
        add_df.to_sql(name="sale_ticket", con=engine, if_exists="append", index=False)
        print(f"新增工单 {len(add_df)} 条入库")

    # 2 更新数据【修复：不再用unique_key字段查询，改用三字段精准匹配删除旧数据】
    if len(update_df) > 0:
        del_sql_list = []
        for _, row in update_df.iterrows():
            dt = row["create_time"]
            cr = escape_sql_str(row["creator"])
            sk = escape_sql_str(row["sku"])
            sql_del = text(f"DELETE FROM sale_ticket WHERE create_time = '{dt}' AND creator = '{cr}' AND sku = '{sk}'")
            del_sql_list.append(sql_del)
        # 执行删除旧记录
        with engine.connect() as conn:
            for s in del_sql_list:
                conn.execute(s)
            conn.commit()
        # 插入最新数据
        update_df.drop(columns="uk_temp", inplace=True)
        update_df = update_df[table_cols]
        update_df.to_sql("sale_ticket", con=engine, if_exists="append", index=False)
        print(f"更新工单 {len(update_df)} 条")

    # 3 逻辑删除（已删除工单标记is_delete=1）
    if len(delete_keys) > 0:
        del_sql_list = []
        for uk in delete_keys:
            dt_str, creator, sku = uk.split("_", 2)
            cr = escape_sql_str(creator)
            sk = escape_sql_str(sku)
            sql_upd = text(f"""
                UPDATE sale_ticket 
                SET is_delete = 1 
                WHERE create_time = '{dt_str}' AND creator = '{cr}' AND sku = '{sk}'
            """)
            del_sql_list.append(sql_upd)
        with engine.connect() as conn:
            for s in del_sql_list:
                conn.execute(s)
            conn.commit()
        print(f"逻辑标记删除工单 {len(delete_keys)} 条（原始数据保留）")

    print("===== 数据同步完成 ====")

if __name__ == "__main__":
    sync_excel_to_mysql()

