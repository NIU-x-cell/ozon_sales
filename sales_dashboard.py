import streamlit as st
import pandas as pd
import plotly.express as px
from config import engine
from io import BytesIO
from datetime import timedelta

# 第二部分：导出函数（放在这里）
@st.cache_data
def to_xlsx(data):
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        data.to_excel(writer, index=False)
    output.seek(0)
    return output.read()

st.set_page_config(page_title="Ozon跟卖工单看板", layout="wide")
st.title("跨境Ozon跟卖工单监控仪表盘")

# 增加数据缓存，5分钟只读取一次数据库
@st.cache_data(ttl=300)
def load_raw_data():
    sql = "SELECT * FROM sale_ticket WHERE is_delete=0"
    df = pd.read_sql(sql, engine)
    df["create_date"] = pd.to_datetime(df["create_date"]).dt.date
    return df

# 全局只加载一次原始数据
df = load_raw_data()

# 侧边筛选
with st.sidebar:
    st.header("筛选条件")
    # 自动默认最近7天
    today = df["create_date"].max()
    start_date = today - timedelta(days=6)
    end_date = today

    start_date = st.date_input("起始日期", value=start_date)
    end_date = st.date_input("结束日期", value=end_date)

    mgrs = df["manager"].unique().tolist()
    sel_mgr = st.multiselect("负责人", ["全部"] + mgrs, default=["全部"])
    status = df["deal_status"].unique()
    sel_status = st.multiselect("处理状态", ["全部"] + list(status), default=["全部"])


# 筛选逻辑
df_filter = df[(df["create_date"] >= start_date) & (df["create_date"] <= end_date)]
if "全部" not in sel_mgr:
    df_filter = df_filter[df_filter["manager"].isin(sel_mgr)]
if "全部" not in sel_status:
 df_filter = df_filter[df_filter["deal_status"].isin(sel_status)]

# ===================== 新增周报环比计算模块（无改动原有代码） =====================
# 计算上周同期时间区间
last_week_start = start_date - timedelta(days=7)
last_week_end = end_date - timedelta(days=7)
# 上周同口径数据集
df_last_week = df[(df["create_date"] >= last_week_start) & (df["create_date"] <= last_week_end)]
if "全部" not in sel_mgr:
    df_last_week = df_last_week[df_last_week["manager"].isin(sel_mgr)]
if "全部" not in sel_status:
    df_last_week = df_last_week[df_last_week["deal_status"].isin(sel_status)]

# 环比计算通用函数（避免重复代码、除0保护）
def calc_ring(curr, last):
    if last == 0:
        return "上周无数据可对比"
    diff = curr - last
    ratio = round(diff / last * 100, 1)
    if ratio > 0:
        return f":green[↑{ratio}%] 上期基数{last}"
    elif ratio < 0:
        return f":red[↓{abs(ratio)}%] 上期基数{last}"
    else:
        return f"持平 上期基数{last}"

# 全局核心指标本周/上周数值
curr_total = len(df_filter)
last_total = len(df_last_week)

curr_unfinish = len(df_filter[df_filter["deal_status"]=="未完成"])
last_unfinish = len(df_last_week[df_last_week["deal_status"]=="未完成"])

curr_trans = df_filter["is_transfer"].sum()
last_trans = df_last_week["is_transfer"].sum()

curr_overtime = df_filter["is_overtime"].sum()
last_overtime = df_last_week["is_overtime"].sum()

curr_repeat_sku = df_filter["repeat_sku"].sum()
last_repeat_sku = df_last_week["repeat_sku"].sum()

curr_op_level = len(df_filter[df_filter["ticket_level"] == "运营层工单"])
last_op_level = len(df_last_week[df_last_week["ticket_level"] == "运营层工单"])
# ==============================================================================

# 1 核心KPI
st.subheader("一、全局核心指标")
# 保留周期文字说明
st.markdown(f"""
> 统计周期：{start_date} ~ {end_date} | 上周同期对比：{last_week_start} ~ {last_week_end}
""")
c1,c2,c3,c4,c5,c6 = st.columns(6)
total = len(df_filter)
unfinish = len(df_filter[df_filter["deal_status"]=="未完成"])
transfer = df_filter["is_transfer"].sum()
overtime = df_filter["is_overtime"].sum()
invalid = df_filter["invalid_sku"].sum()
avg_h = round(df_filter["deal_hour"].mean(),1)

# 提取环比数字，去掉文字符号，仅保留百分比数字给delta
def get_delta_num(curr, last):
    if last == 0:
        return None
    return round((curr - last) / last * 100, 1)

delta_total = get_delta_num(curr_total, last_total)
delta_unfinish = get_delta_num(curr_unfinish, last_unfinish)
delta_trans = get_delta_num(curr_trans, last_trans)
delta_over = get_delta_num(curr_overtime, last_overtime)

# 指标卡内置环比delta，自动红绿箭头
c1.metric("总工单", total, delta=f"{delta_total}%" if delta_total is not None else "无上周数据")
c2.metric("未完成", unfinish, delta=f"{delta_unfinish}%" if delta_unfinish is not None else "无上周数据")
c3.metric("移交管理", transfer, delta=f"{delta_trans}%" if delta_trans is not None else "无上周数据")
c4.metric("超时工单", overtime, delta=f"{delta_over}%" if delta_over is not None else "无上周数据")
c5.metric("空SKU无效工单", invalid)
c6.metric("平均时效(h)", avg_h)

# 【数据总结：纯结果输出】
st.success(f"""
📊 本期核心数据小结：
\n
1. 本期合计工单{total}条，整体环比{calc_ring(curr_total, last_total).split(' ')[0]}；
\n
2. 当前未完成工单{unfinish}条、移交工单{transfer}条、超时工单{overtime}条，整体处理时效{avg_h}小时；
\n
3. 无效工单{invalid}条，出现的原因是填写不规范，应再次明确填写要求以及减少空工单。
""")
# ==== 新增：超时工单明细查看+导出 ====
st.subheader("超时工单明细")
overtime_df = df_filter[df_filter["is_overtime"] == 1]
st.dataframe(overtime_df, height=300)
# 导出超时工单
overtime_bytes = to_xlsx(overtime_df)
st.download_button(label="导出超时工单", data=overtime_bytes, file_name="超时工单明细.xlsx")

# 【数据总结】
st.success(f"""
⏱️ 超时数据小结
本期累计{overtime}条超时工单，可通过明细定位具体超时人员与超时SKU。
""")

# 2 时间趋势
st.subheader("二、工单时间趋势")
# 新增本周工单总量环比提示（仅新增）
st.markdown(f"本周工单总量{curr_total}条，环比{calc_ring(curr_total, last_total).split(' ')[0]}")
col1, col2 = st.columns(2)

day_df = df_filter.groupby("create_date").size().reset_index(name="工单量")
fig_day = px.line(day_df, x="create_date", y="工单量", title="每日工单趋势")
# 折线图数值：显示+放大字号
fig_day.update_traces(
    texttemplate="%{y}",
    textposition="top center",
    textfont_size=16  # 文字大小
)
fig_day.update_layout(uniformtext_minsize=12, uniformtext_mode='show')
# 增加唯一key
col1.plotly_chart(fig_day, use_container_width=True, key="chart_day_line")

# 星期柱状图（修复乱序+显示数字+大字体）
# 1. 定义标准星期顺序
week_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
week_df = df_filter.groupby("weekday").size().reset_index(name="工单量")
# 2. 强制按周一到周日排序
week_df["weekday"] = pd.Categorical(week_df["weekday"], categories=week_order, ordered=True)
week_df = week_df.sort_values("weekday")

fig_week = px.bar(week_df, x="weekday", y="工单量", title="星期分布")
# 柱状图数值：显示+放大字号
fig_week.update_traces(
    texttemplate="%{y}",
    textposition="outside",
    textfont_size=16
)
fig_week.update_layout(uniformtext_minsize=12, uniformtext_mode='show')
# 换一个不同key
col2.plotly_chart(fig_week, use_container_width=True, key="chart_week_bar")

# 【数据总结】
peak_day = day_df.loc[day_df["工单量"].idxmax(), "create_date"]
peak_num = day_df["工单量"].max()
week_peak = week_df.loc[week_df["工单量"].idxmax(), "weekday"]
st.success(f"""
📈 时序数据小结
本期工单峰值日出现在{peak_day}，单日最高工单{peak_num}条；周维度峰值出现在{week_peak}。
""")

# 3 处理状态饼图
st.subheader("三、处理状态占比")
status_df = df_filter["deal_status"].value_counts().reset_index()
fig_status = px.pie(status_df, values="count", names="deal_status", hole=0.3)

# 饼图内部百分比+原始数量，放大文字
fig_status.update_traces(
    texttemplate="%{percent:.1%}<br>数量：%{value}",
    textfont_size=14,
)
# 图例字号放大
fig_status.update_layout(
    legend_font_size=15
)
st.plotly_chart(fig_status, use_container_width=True, key="chart_status_pie")

# 【数据总结】
finish_num = total - unfinish
finish_rate = round(finish_num / total * 100, 1) if total > 0 else 0
st.success(f"""
🥧 工单闭环小结
本期工单已完成{finish_num}条，整体闭环率{finish_rate}%，未完成工单占比偏高则存在积压风险，需及时清单。
""")

# 4 负责人/组长TOP
st.subheader("四、责任主体排行")
ca,cb = st.columns(2)
mgr_top = df_filter["manager"].value_counts().head(10).reset_index()
fig_mgr = px.bar(mgr_top, x="manager", y="count", title="负责人TOP10")
# 柱子顶部显示数字，字号16
fig_mgr.update_traces(
    texttemplate="%{y}",
    textposition="outside",
    textfont_size=15
)
ca.plotly_chart(fig_mgr, width="stretch", key="chart_mgr_bar")

leader_top = df_filter["team_leader"].value_counts().head(10).reset_index()
fig_leader = px.bar(leader_top, x="team_leader", y="count",title="组长TOP10")
# 柱子顶部显示数字，字号16
fig_leader.update_traces(
    texttemplate="%{y}",
    textposition="outside",
    textfont_size=15
)
cb.plotly_chart(fig_leader, width="stretch", key="chart_leader_bar")

# 【数据总结】
top_mgr_name = mgr_top.iloc[0]["manager"] if len(mgr_top) > 0 else "无"
top_mgr_cnt = mgr_top.iloc[0]["count"] if len(mgr_top) > 0 else 0
st.success(f"""
👤 人员负荷小结
本期工单处理量最高负责人为{top_mgr_name}，工单量{top_mgr_cnt}条，一周内超过30条的负责人和组长都应注意。
""")

# 五。明细+导出（新增负责人筛选下拉条）
st.subheader("五、工单明细查询导出")

# 2. 先定义表格列（放到最前面！）
show_cols = [
    "create_time", "creator", "shop_id", "sku", "operator",
    "team_leader", "manager", "deal_status", "transfer_type",
 "real_deal_hour", "is_overtime", "is_transfer"
]

# 1. 负责人多选下拉
manager_count = df_filter["manager"].value_counts()
all_managers = manager_count.index.tolist()

selected_managers = st.multiselect(
    label="按负责人筛选（支持多选）",
 options=all_managers,
    default=all_managers,
    key="detail_manager_multi_filter"
)

# 筛选语句（现在show_cols已经提前定义好了，不会报错）
df_detail = df_filter[df_filter["manager"].isin(selected_managers)][show_cols].copy()

# 渲染表格
st.dataframe(df_detail, height=400, use_container_width=True)

# 导出按钮
down_data = to_xlsx(df_detail)
st.download_button(
    label="导出当前筛选数据",
    data=down_data,
    file_name="工单明细.xlsx",
    key="detail_export_btn"
)

# 【数据总结】
st.success(f"""
📋 明细数据小结
当前筛选条件下有效明细数据共{len(df_detail)}条，支持按需导出归档、对账复盘。
""")
# 6 异常分析模块

st.subheader("六、核心异常分析")
# 移交工单环比提示（仅新增）
st.markdown(f"本周移交管理工单{curr_trans}条，环比{calc_ring(curr_trans, last_trans).split(' ')[0]}")
# 各负责人移交占比
trans_agg = df_filter.groupby("manager").agg(
    total=("id", "count"),
    trans_num=("is_transfer", "sum")
).reset_index()
trans_agg["移交占比%"] = round(trans_agg["trans_num"]/trans_agg["total"]*100,2)

# 新增：按移交占比从高到低排序
trans_agg = trans_agg.sort_values("移交占比%", ascending=False)

fig_trans = px.bar(trans_agg, x="manager", y="移交占比%", title="各部门移交管理工单占部门总提交数比例")
fig_trans.update_traces(
# 柱子顶部显示百分比，放大字体
 texttemplate="%{y}%",
    textposition="outside",
    textfont_size=16
)
st.plotly_chart(fig_trans, use_container_width=True)

# 高频重复SKU
sku_top = df_filter["sku"].value_counts().head(10).reset_index()
fig_sku = px.bar(sku_top, x="sku", y="count", title="高频跟卖SKU TOP10")
sku_top["sku"] = sku_top["sku"].astype(str)
fig_sku.update_traces(texttemplate="%{y}", textposition="outside", width=0.8)
fig_sku.update_layout(height=550, bargap=0.2, xaxis_tickangle=-45)
st.plotly_chart(fig_sku, use_container_width=True)

# 下拉选择框，SKU数字从大到小排序
sku_list = sorted(sku_top["sku"].tolist(), key=lambda x: int(x), reverse=True)
selected_sku = st.selectbox("选择跟卖SKU查看全部工单", sku_list)
detail = df_filter[df_filter["sku"].astype(str)==selected_sku]
st.dataframe(detail, use_container_width=True)
top_sku = sku_top.iloc[0]["sku"] if len(sku_top) > 0 else "无"

# 【数据总结】
top_sku_cnt = sku_top.iloc[0]["count"] if len(sku_top) > 0 else 0
max_trans_mgr = trans_agg.iloc[0]["manager"] if len(trans_agg) > 0 else "无"
max_trans_rate = trans_agg.iloc[0]["移交占比%"] if len(trans_agg) > 0 else 0
st.success(f"""
⚠️ 异常数据小结
本期最高风险SKU为{top_sku}，累计跟卖工单{top_sku_cnt}条；移交管理处的被跟单为{max_trans_mgr}，移交占本部门的比例为{max_trans_rate}%。
""")

# 7.工单层级饼
st.subheader("七、不同层级工单占比")
# 运营层工单环比提示（仅新增）
st.markdown(f"一线运营工单{curr_op_level}条，环比{calc_ring(curr_op_level, last_op_level).split(' ')[0]}")
level_df = df_filter["ticket_level"].value_counts().reset_index()
fig_level = px.pie(level_df, values="count", names="ticket_level",title="不同层级工单占比")

# 饼内同时显示数量+百分比，放大内部文字
fig_level.update_traces(
    texttemplate="%{percent:.2%}<br>工单数量：%{value}",
 textfont_size=15
)
# 调整右侧图例文字大小
fig_level.update_layout(
    legend_font_size=14
)
st.plotly_chart(fig_level, use_container_width=True)

# 【数据总结】
op_ratio = round(curr_op_level / total * 100,2) if total >0 else 0
st.success(f"""
🏷️ 工单层级小结
本期一线运营工单{curr_op_level}条，占整体工单{op_ratio}%，团队基础问题处理体量稳定，工单难度结构正常波动。
""")

# 异常总结
st.subheader("八、高占比异常总结")
# 各负责人移交占比
trans_agg = df_filter.groupby("manager").agg(
    transfer=("is_transfer", "sum"),
    total=("manager", "count")
).reset_index()
# 新增移交占比计算列
trans_agg["移交占比"] = trans_agg["transfer"] / trans_agg["total"]
# 占比数值
trans_rate = round(trans_agg["移交占比"].max()*100,2)
# 取占比最高负责人
max_trans_mgr = trans_agg.sort_values(by="移交占比", ascending=False).iloc[0]["manager"]
repeat_total = df_filter["repeat_sku"].sum()

st.write(f"""
1. 部门无法处理移交工单占比 {trans_rate}%，最高风险负责人：{max_trans_mgr}；
2. 超时积压工单{overtime}条，存在流程滞后风险；
3. 重复恶意跟卖SKU共{repeat_total}条；
""")
# 新增：恶意跟卖工单 先展示表格、再导出
st.subheader("恶意跟卖工单明细")
repeat_sku_df = df_filter[df_filter["repeat_sku"] == 1]
# 先展示表格
st.dataframe(repeat_sku_df, height=300)
# 再提供导出按钮
st.download_button(
    label="导出恶意跟卖工单明细",
 data=to_xlsx(repeat_sku_df),
    file_name="恶意跟卖工单.xlsx"
)

# 【数据总结】
st.success(f"""
🔴 恶意跟卖小结
本期识别{repeat_total}条恶意重复跟卖工单，属于竞品定向打击行为，需对高频SKU重点维权防护。
""")