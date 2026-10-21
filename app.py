import os
from datetime import datetime, timedelta
import pandas as pd
import streamlit as st
import engine as E

st.set_page_config(page_title="D2C Order Follow-up", page_icon="📦", layout="wide")
SAMPLE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "sample_orders.csv")
COLS = ["id", "order_id", "customer_name", "phone", "exc_type", "reason", "priority", "assigned_to", "action",
        "deadline", "status", "overdue"]

# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.title("📦 Order Follow-up")
    now = datetime.now().replace(microsecond=0)
    if st.checkbox("Override current time (demo)"):
        d = st.date_input("Date", now.date())
        t = st.time_input("Time", now.time().replace(second=0))
        now = datetime.combine(d, t)
    st.caption(f"Reference time: {now:%d %b %Y %H:%M}")
    emps = E.employees()
    user = st.selectbox("Acting as (audit user)", ["Manager"] + emps)
    is_mgr = user == "Manager"
    st.divider()
    st.subheader("Business rules")
    cfg = E.get_cfg()
    for k in cfg:  # load saved values into widget state once; widgets keep fixed keys so they never reset
        st.session_state.setdefault("cfg_" + k, float(cfg[k]))
    with st.form("rules"):
        st.number_input("Unconfirmed flag after (hours)", min_value=0.0, step=0.5, key="cfg_unconfirmed_hours")
        st.number_input("Dispatch deadline (hours)", min_value=0.0, step=1.0, key="cfg_dispatch_hours")
        st.number_input("High-value threshold (₹)", min_value=0.0, step=500.0, key="cfg_high_value")
        if st.form_submit_button("Save & re-run rules", disabled=not is_mgr):
            E.save_cfg({k: st.session_state["cfg_" + k] for k in cfg})
            st.session_state["flash"] = f"Rules saved. {E.rerun_rules(now)} new exception(s) created."
            st.rerun()
    if "flash" in st.session_state:
        st.success(st.session_state.pop("flash"))
    if not is_mgr:
        st.caption("Only the Manager can change rules.")


# ------------------------------------------------------------------ helpers
def filters(df, key):
    cols = st.columns(4)
    mask = pd.Series(True, index=df.index)
    od = pd.to_datetime(df["order_date"], errors="coerce")
    if od.notna().any():
        rng = cols[0].date_input("Order date range", (od.min().date(), od.max().date()), key=key + "d")
        if len(rng) == 2:
            mask &= od.isna() | ((od >= pd.Timestamp(rng[0])) & (od < pd.Timestamp(rng[1]) + timedelta(days=1)))
    for i, (label, col, opts) in enumerate([("Exception type", "exc_type", E.EXC_TYPES),
                                            ("Employee", "assigned_to", sorted(df["assigned_to"].unique())),
                                            ("Priority", "priority", ["High", "Medium", "Low"])], start=1):
        sel = cols[i].multiselect(label, opts, key=key + col)
        if sel:
            mask &= df[col].isin(sel)
    return df[mask]


def run_import(src, name):
    try:
        st.session_state["last_import"] = E.process_upload(E.read_file(src, name), name, now, user)
        st.session_state["last_import_name"] = name
        st.session_state.pop("import_error", None)
    except Exception as ex:
        st.session_state["import_error"] = f"Import failed: {ex}"


def show_import():  # rendered from session_state so it survives reruns (rule/time/widget changes)
    if "import_error" in st.session_state:
        st.error(st.session_state["import_error"])
    res = st.session_state.get("last_import")
    if not res:
        return
    st.success(f"Last import ({st.session_state['last_import_name']}): {res['total']} rows, {res['valid']} valid, "
               f"{len(res['issues'])} with data problems. {res['new_rules']} new rule exceptions, "
               f"{res['new_data']} new data-correction tasks (re-uploading the same file creates 0 new tasks).")
    if res["issues"]:
        st.subheader("Records needing data correction")
        st.dataframe(pd.DataFrame(res["issues"]).rename(columns={"row": "file_row"}), use_container_width=True, hide_index=True)


# ------------------------------------------------------------------ tabs
tabs = st.tabs(["📊 Dashboard", "⬆️ Upload", "✅ Tasks", "📄 Daily Report", "🧾 Audit Trail"])

with tabs[1]:
    st.subheader("Import orders (CSV / Excel)")
    st.caption("Required columns: " + ", ".join(E.COLUMNS))
    up = st.file_uploader("Choose file", type=["csv", "xlsx", "xls"])
    c1, c2 = st.columns(2)
    if c1.button("Import uploaded file", disabled=up is None):
        run_import(up, up.name)
    if c2.button("Load bundled sample data"):
        if os.path.exists(SAMPLE):
            run_import(SAMPLE, "sample_orders.csv")
        else:
            st.error("Sample file missing - run: python gen_data.py")
    show_import()

ex_all = E.with_overdue(E.exceptions_df(), now)

with tabs[0]:
    if ex_all.empty:
        st.info("No data yet - go to the Upload tab and load the sample data.")
    else:
        f = filters(ex_all, "dash")
        unres = f[f["status"] != "Resolved"]
        m = st.columns(5)
        m[0].metric("Total orders uploaded", E.latest_total())
        m[1].metric("Orders requiring attention", unres["order_id"].nunique())
        m[2].metric("Open tasks", int((f["status"] == "Open").sum()))
        m[3].metric("In progress", int((f["status"] == "In Progress").sum()))
        m[4].metric("Overdue tasks", int(f["overdue"].sum()))
        l, r = st.columns(2)
        l.subheader("Exceptions by category")
        l.bar_chart(f.groupby("exc_type").size())
        r.subheader("Employee-wise pending tasks")
        r.bar_chart(unres.groupby("assigned_to").size())
        st.subheader("🔴 High-priority cases requiring immediate action")
        hp = unres[unres["priority"] == "High"].sort_values("deadline")
        st.dataframe(hp[COLS], use_container_width=True, hide_index=True)

with tabs[2]:
    if ex_all.empty:
        st.info("No tasks yet.")
    else:
        f = filters(ex_all, "task")
        stat = st.multiselect("Task status", E.TASK_STATUSES, default=["Open", "In Progress"], key="tstat")
        f = f[f["status"].isin(stat)] if stat else f
        st.dataframe(f[COLS], use_container_width=True, hide_index=True)
        opts = {f"#{r.id} | {r.order_id} | {r.exc_type} | {r.assigned_to} | {r.status}": int(r.id) for r in f.itertuples()}
        if opts:
            tid = opts[st.selectbox("Select a task to update", list(opts))]
            row = ex_all[ex_all["id"] == tid].iloc[0]
            st.markdown(f"**{row.exc_type}** · {row.customer_name} ({row.phone}) · deadline {row.deadline}"
                        + (" · 🔴 OVERDUE" if row.overdue else ""))
            st.caption(f"{row.reason} → {row.action}")
            c1, c2 = st.columns(2)
            with c1:
                ns = st.selectbox("New status", E.TASK_STATUSES, index=E.TASK_STATUSES.index(row.status))
                if st.button("Update status"):
                    try:
                        E.set_status(tid, ns, user, now); st.rerun()
                    except Exception as ex:
                        st.error(str(ex))
            with c2:
                if is_mgr:
                    pool = sorted(set(emps) | {row.assigned_to} - {"Unassigned"})
                    emp = st.selectbox("Reassign to", pool, index=pool.index(row.assigned_to) if row.assigned_to in pool else 0)
                    rd = st.checkbox("Reset deadline from now", value=bool(row.overdue))
                    if st.button("Reassign"):
                        try:
                            E.reassign(tid, emp, user, now, rd); st.rerun()
                        except Exception as ex:
                            st.error(str(ex))
                else:
                    st.info("Switch to 'Manager' (sidebar) to reassign tasks.")
            hist = E.audit_df()
            st.caption("Task history")
            st.dataframe(hist[hist["task_id"] == tid], use_container_width=True, hide_index=True)

with tabs[3]:
    st.subheader(f"Daily Exception Report - {now:%d %b %Y}")
    rep = E.build_report(now)
    if rep.empty:
        st.info("Nothing to report yet.")
    else:
        st.dataframe(rep, use_container_width=True, hide_index=True)
        st.download_button("⬇️ Download report (CSV)", rep.to_csv(index=False).encode("utf-8"),
                           f"daily_exception_report_{now:%Y%m%d}.csv", "text/csv")

with tabs[4]:
    st.subheader("Audit trail")
    au = E.audit_df()
    q = st.text_input("Filter by order ID")
    if q:
        au = au[au["order_id"].str.contains(q, case=False, na=False)]
    st.dataframe(au, use_container_width=True, hide_index=True)
    st.download_button("⬇️ Download audit (CSV)", au.to_csv(index=False).encode("utf-8"), "audit_trail.csv", "text/csv")
