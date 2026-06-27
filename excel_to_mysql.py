import pandas as pd
import numpy as np
from sqlalchemy import text
from config import engine
import warnings

warnings.filterwarnings("ignore")

def escape_sql_str(val):
    if pd.isna(val):
        return ""
    return str(val).replace("'", "''")

# ========== 1 读取清洗Excel（字段名完全保留，无修改） ==========
def load_clean_excel():
    # 读取Excel
    df_raw = pd.read_excel("./跟卖处理表/跟卖处理表.xlsx")

    # 列名映射（和原代码完全一致，无修改）
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

    # 清理冗余列、空行
    if "@负责人" in df_raw.columns:
        df_raw.drop(columns=["@负责人"], inplace=True)
    df = df_raw.dropna(how="all")

    # 补全缺失列（和原代码一致）
    if "img2" not in df.columns:
        df["img2"] = ""

    # 时间转换（和原代码逻辑一致，简化写法）
    df["create_time"] = pd.to_datetime(df["create_time"], errors="coerce")
    df["update_time"] = pd.to_datetime(df["update_time"], errors="coerce")
    df["create_date"] = df["create_time"].dt.date
    df["weekday"] = df["create_time"].dt.day_name()
    df["hour"] = df["create_time"].dt.hour

    # 计算真实处理时效（和原代码一致）
    df["real_deal_hour"] = round(
        (df["update_time"] - df["create_time"]).dt.total_seconds() / 3600, 2
    )

    # 工单层级（和原代码一致）
    df["ticket_level"] = np.select(
        [df["operator"].notna(), df["team_leader"].notna()],
        ["运营层工单", "组长层工单"],
        default="负责人层工单"
    )

    # 异常标记（和原代码完全一致，无修改）
    df["is_overtime"] = ((df["deal_status"] == "未完成") & (df["real_deal_hour"] > 72)).astype(int)
    df["is_transfer"] = df["transfer_type"].notna().astype(int)
    df["invalid_sku"] = df["sku"].isna().astype(int)
    repeat_sku = df["sku"].value_counts()[df["sku"].value_counts() >= 3].index
    df["repeat_sku"] = df["sku"].isin(repeat_sku).astype(int)
    df["is_delete"] = 0

    # 数据库标准字段（和原代码完全一致，无修改）
    table_cols = [
        "create_time", "creator", "shop_id", "sku", "shop_name", "operator", "team_leader", "manager",
        "deal_status", "remark", "img1", "img2", "transfer_type", "update_time", "deal_hour",
        "real_deal_hour", "ticket_level", "is_overtime", "is_transfer", "invalid_sku", "repeat_sku",
        "create_date", "weekday", "hour", "is_delete"
    ]
    return df[table_cols]


# ========== 2 同步主逻辑（简化批量操作，彻底解决卡顿） ==========
def sync_excel_to_mysql():
    print("1/4 开始读取并清洗Excel数据")
    df_excel = load_clean_excel()
    print(f"Excel清洗完成，共 {len(df_excel)} 条有效数据")

    print("2/4 开始读取数据库历史数据")
    df_db = pd.read_sql("SELECT * FROM sale_ticket", engine)
    print(f"数据库读取完成，共 {len(df_db)} 条历史数据")

    # 空库直接全量新增
    if df_db.empty:
        print("数据库无历史数据，开始全量新增")
        df_excel.to_sql("sale_ticket", con=engine, if_exists="append", index=False)
        print(f"✅ 全量新增完成，共 {len(df_excel)} 条数据入库")
        print("===== 数据同步全部完成 ====")
        return

    # 构造唯一主键（和原代码逻辑一致，简化写法）
    df_excel["uk_temp"] = (
            df_excel["create_time"].fillna(pd.Timestamp("1970-01-01")).dt.strftime("%Y-%m-%d %H:%M:%S")
            + "_" + df_excel["creator"].fillna("")
            + "_" + df_excel["sku"].fillna("")
    )
    df_db["uk_temp"] = (
            df_db["create_time"].fillna(pd.Timestamp("1970-01-01")).dt.strftime("%Y-%m-%d %H:%M:%S")
            + "_" + df_db["creator"].fillna("")
            + "_" + df_db["sku"].fillna("")
    )

    # 区分新增/更新/删除数据
    excel_keys = set(df_excel["uk_temp"].unique())
    db_keys = set(df_db["uk_temp"].unique())
    add_df = df_excel[~df_excel["uk_temp"].isin(db_keys)].copy()
    update_df = df_excel[df_excel["uk_temp"].isin(db_keys)].copy()
    delete_keys = list(db_keys - excel_keys)

    print(f"3/4 数据对比完成：新增 {len(add_df)} 条，更新 {len(update_df)} 条，删除 {len(delete_keys)} 条")

    # 1 新增数据
    if len(add_df) > 0:
        add_df.drop(columns="uk_temp", inplace=True)
        add_df.to_sql("sale_ticket", con=engine, if_exists="append", index=False)
        print(f"✅ 新增工单 {len(add_df)} 条入库完成")

    # 2 更新数据（批量删除旧数据，不用uk_temp字段）
    if len(update_df) > 0:
        # 拼接多条删除条件
        del_conditions = []
        for _, row in update_df.iterrows():
            t = row["create_time"].strftime("%Y-%m-%d %H:%M:%S") if pd.notna(
                row["create_time"]) else "1970-01-01 00:00:00"
            cr = escape_sql_str(row["creator"])
            sk = escape_sql_str(row["sku"])
            del_conditions.append(f"(create_time='{t}' AND creator='{cr}' AND sku='{sk}')")

        # 合并为一条SQL，只执行一次，不会卡顿
        del_sql = text(f"DELETE FROM sale_ticket WHERE {' OR '.join(del_conditions)}")
        with engine.connect() as conn:
            conn.execute(del_sql)
            conn.commit()

        # 插入新数据
        update_df.drop(columns="uk_temp", inplace=True)
        update_df.to_sql("sale_ticket", con=engine, if_exists="append", index=False)
        print(f"✅ 更新工单 {len(update_df)} 条完成")

    # 3 逻辑删除（改回三字段条件）
    if len(delete_keys) > 0:
        update_conditions = []
        for uk in delete_keys:
            dt_str, creator, sku = uk.split("_", 2)
            cr = escape_sql_str(creator)
            sk = escape_sql_str(sku)
            # 把外层双引号去掉，避免冲突
            update_conditions.append(f"(create_time='{dt_str}' AND creator='{cr}' AND sku='{sk}')")

        # 拼接条件
        cond_str = " OR ".join(update_conditions)
        upd_sql = text(f"UPDATE sale_ticket SET is_delete = 1 WHERE {cond_str}")

        with engine.connect() as conn:
            conn.execute(upd_sql)
            conn.commit()
        print(f"✅ 逻辑标记删除工单 {len(delete_keys)} 条完成")

    print("4/4 全部操作完成")
    print("===== 数据同步全部完成 ====")


if __name__ == "__main__":
    sync_excel_to_mysql()
