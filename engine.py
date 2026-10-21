"""Validation, rule engine, task + audit logic (SQLite persistence)."""
import os, re, sqlite3, warnings
from datetime import datetime, timedelta
import pandas as pd

warnings.simplefilter("ignore")
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "app.db")
FMT = "%Y-%m-%d %H:%M:%S"
COLUMNS = ["order_id", "order_date", "customer_name", "phone", "order_value", "payment_type",
           "order_status", "dispatch_date", "expected_delivery_date", "assigned_employee"]
STATUSES = ["Unconfirmed", "Confirmed", "Dispatched", "Delivered", "Delivery Failed", "Cancelled"]
PAYMENTS = ["Prepaid", "COD"]
TASK_STATUSES = ["Open", "In Progress", "Resolved"]
DEFAULT_CFG = {"unconfirmed_hours": 2.0, "dispatch_hours": 24.0, "high_value": 5000.0}
DEFAULT_EMPLOYEES = ["Anita", "Rahul", "Priya", "Suresh", "Meera"]

# per exception type: (priority, hours allowed to act after breach, recommended action)
RULES = {
    "Unconfirmed":        ("Medium", 4,  "Call customer to confirm the order"),
    "Dispatch Delay":     ("High",   4,  "Check with warehouse and dispatch immediately"),
    "Shipment Delay":     ("Medium", 24, "Contact courier for status and update the customer"),
    "Failed Delivery":    ("High",   2,  "Call customer and arrange re-delivery"),
    "High-Value Overdue": ("High",   2,  "Escalate to courier/manager and update the customer"),
    "Data Correction":    ("Low",    24, "Fix the record in the source file and re-upload"),
}
EXC_TYPES = list(RULES)
# an open exception auto-resolves when the order's status is no longer one of these
STILL_VALID = {"Unconfirmed": ("Unconfirmed",), "Dispatch Delay": ("Confirmed",),
               "Shipment Delay": ("Dispatched",), "Failed Delivery": ("Delivery Failed",),
               "High-Value Overdue": ("Dispatched", "Delivery Failed")}

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders(order_id TEXT PRIMARY KEY, order_date TEXT, customer_name TEXT, phone TEXT,
  order_value REAL, payment_type TEXT, order_status TEXT, dispatch_date TEXT, expected_delivery_date TEXT, assigned_employee TEXT);
CREATE TABLE IF NOT EXISTS exceptions(id INTEGER PRIMARY KEY AUTOINCREMENT, ukey TEXT UNIQUE, order_id TEXT,
  customer_name TEXT, phone TEXT, order_value REAL, order_date TEXT, exc_type TEXT, reason TEXT, priority TEXT,
  assigned_to TEXT, action TEXT, deadline TEXT, status TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT, task_id INTEGER, action TEXT, ts TEXT, user TEXT);
CREATE TABLE IF NOT EXISTS uploads(id INTEGER PRIMARY KEY AUTOINCREMENT, filename TEXT, ts TEXT, total_rows INT, valid_rows INT, issue_rows INT, user TEXT);
CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v REAL);
"""


def conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c


def _f(t):
    return t.strftime(FMT) if t else None


def _p(s):
    return datetime.fromisoformat(s) if s else None


def _blank(v):
    return v is None or str(v).strip().lower() in ("", "nan", "nat", "none")


def _parse_dt(v):
    try:
        v = str(v).strip()
        t = pd.to_datetime(v, dayfirst=not re.match(r"^\d{4}-", v))  # ISO stays year-first; else DD-MM-YYYY
        return None if pd.isna(t) else t.to_pydatetime()
    except Exception:
        return None


# ---------------------------------------------------------------- import + validation
def read_file(src, name):
    n = name.lower()
    if n.endswith(".csv"):
        df = pd.read_csv(src, dtype=str, keep_default_na=False)
    elif n.endswith((".xlsx", ".xls")):
        df = pd.read_excel(src, dtype=str, keep_default_na=False)
    else:
        raise ValueError("Unsupported file type - upload a .csv or .xlsx file")
    df.columns = [str(c).strip().lower().replace(" ", "_").replace("-", "_") for c in df.columns]
    missing = [c for c in COLUMNS if c not in df.columns]
    if missing:
        raise ValueError("Missing columns: " + ", ".join(missing))
    if df.empty:
        raise ValueError("The file has no rows")
    return df[COLUMNS]


def validate(df):
    """Returns (clean_rows, issue_rows). Rows with any problem are NOT rule-evaluated."""
    clean, issues, seen = [], [], {}
    for i, r in enumerate(df.to_dict("records")):
        rown = i + 2  # header is row 1
        g = lambda k: "" if _blank(r[k]) else str(r[k]).strip()
        P = []
        oid = g("order_id")
        if not oid:
            P.append("Missing order_id")
        elif oid in seen:
            P.append(f"Duplicate order ID (first seen in row {seen[oid]})")
        else:
            seen[oid] = rown
        for k in ("customer_name", "phone", "order_value", "payment_type", "order_status", "assigned_employee", "order_date"):
            if not g(k):
                P.append(f"Missing {k}")
        status = pay = val = None
        if g("order_status"):
            status = next((s for s in STATUSES if s.lower() == g("order_status").lower()), None)
            if not status:
                P.append(f"Invalid order_status '{g('order_status')}'")
        if g("payment_type"):
            pay = next((s for s in PAYMENTS if s.lower() == g("payment_type").lower()), None)
            if not pay:
                P.append(f"Invalid payment_type '{g('payment_type')}'")
        digits = re.sub(r"\D", "", g("phone"))
        if len(digits) == 12 and digits.startswith("91"):
            digits = digits[2:]
        if g("phone") and len(digits) != 10:
            P.append(f"Invalid phone '{g('phone')}'")
        if g("order_value"):
            try:
                val = float(g("order_value").replace(",", "").replace("₹", ""))
                if val <= 0:
                    P.append("order_value must be > 0")
            except ValueError:
                P.append(f"Invalid order_value '{g('order_value')}'")
        need = {"order_date": True,
                "dispatch_date": status in ("Dispatched", "Delivered", "Delivery Failed"),
                "expected_delivery_date": status in ("Dispatched", "Delivery Failed")}
        d = {}
        for k, req in need.items():
            if not g(k):
                if req and k != "order_date":
                    P.append(f"Missing {k}")
                d[k] = None
            else:
                d[k] = _parse_dt(g(k))
                if d[k] is None:
                    P.append(f"Invalid date in {k} '{g(k)}'")
        if d["order_date"] and d["dispatch_date"] and d["dispatch_date"] < d["order_date"]:
            P.append("dispatch_date is before order_date")
        if d["order_date"] and d["expected_delivery_date"] and d["expected_delivery_date"] < d["order_date"]:
            P.append("expected_delivery_date is before order_date")
        if P:
            issues.append(dict(row=rown, order_id=oid or f"ROW-{rown}", customer_name=g("customer_name"),
                               phone=g("phone"), order_value=val, order_date=g("order_date"),
                               assigned=g("assigned_employee") or "Unassigned", problems="; ".join(P)))
        else:
            clean.append(dict(order_id=oid, order_date=_f(d["order_date"]), customer_name=g("customer_name"),
                              phone=digits, order_value=val, payment_type=pay, order_status=status,
                              dispatch_date=_f(d["dispatch_date"]), expected_delivery_date=_f(d["expected_delivery_date"]),
                              assigned_employee=g("assigned_employee")))
    return clean, issues


# ---------------------------------------------------------------- audit / exceptions
def audit(c, order_id, task_id, action, now, user):
    c.execute("INSERT INTO audit(order_id,task_id,action,ts,user) VALUES(?,?,?,?,?)",
              (order_id, task_id, action, _f(now), user))


def _add_exc(c, key, order_id, cust, phone, value, order_date, typ, reason, assigned, now, breach):
    prio, sla, action = RULES[typ]
    cur = c.execute(  # UNIQUE ukey (order_id|type) + OR IGNORE = no duplicate tasks on re-upload
        "INSERT OR IGNORE INTO exceptions(ukey,order_id,customer_name,phone,order_value,order_date,exc_type,reason,"
        "priority,assigned_to,action,deadline,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'Open',?)",
        (key, order_id, cust, phone, value, order_date, typ, reason, prio, assigned, action,
         _f(breach + timedelta(hours=sla)), _f(now)))
    if cur.rowcount:
        audit(c, order_id, cur.lastrowid, f"Task created and assigned to {assigned} [{typ}]", now, "System")
    return cur.rowcount


def get_cfg(c=None):
    own = c is None
    c = c or conn()
    cfg = dict(DEFAULT_CFG)
    cfg.update({r["k"]: r["v"] for r in c.execute("SELECT k,v FROM settings")})
    if own:
        c.close()
    return cfg


def save_cfg(cfg):
    c = conn()
    for k, v in cfg.items():
        c.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (k, float(v)))
    c.commit(); c.close()


def run_rules(c, now):
    """Apply the 5 rules to stored (clean) orders. Returns number of NEW exceptions."""
    cfg = get_cfg(c)
    n = 0
    for o in c.execute("SELECT * FROM orders").fetchall():
        st, od, ed, val = o["order_status"], _p(o["order_date"]), _p(o["expected_delivery_date"]), o["order_value"]

        def add(typ, reason, breach):
            nonlocal n
            key = f"{o['order_id']}|{typ}"
            made = _add_exc(c, key, o["order_id"], o["customer_name"], o["phone"], val,
                            o["order_date"], typ, reason, o["assigned_employee"], now, breach)
            n += made
            if not made:  # keep the reason text current (age grows over time)
                c.execute("UPDATE exceptions SET reason=? WHERE ukey=? AND status!='Resolved'", (reason, key))
            if not made:  # task exists: reopen only if the SYSTEM auto-resolved it and the rule applies again
                t = c.execute("SELECT id,status FROM exceptions WHERE ukey=?", (key,)).fetchone()
                last = c.execute("SELECT user FROM audit WHERE task_id=? ORDER BY id DESC LIMIT 1", (t["id"],)).fetchone()
                if t["status"] == "Resolved" and last and last["user"] == "System":
                    c.execute("UPDATE exceptions SET status='Open' WHERE id=?", (t["id"],))
                    audit(c, o["order_id"], t["id"], "Reopened (rule applies again)", now, "System")
                    n += 1

        hrs = (now - od).total_seconds() / 3600
        if st == "Unconfirmed":
            b = od + timedelta(hours=cfg["unconfirmed_hours"])
            if now > b:
                add("Unconfirmed", f"Unconfirmed for {hrs:.1f}h (limit {cfg['unconfirmed_hours']:g}h)", b)
        elif st == "Confirmed":
            b = od + timedelta(hours=cfg["dispatch_hours"])
            if now > b:
                add("Dispatch Delay", f"Confirmed but not dispatched after {hrs:.1f}h (limit {cfg['dispatch_hours']:g}h)", b)
        elif st in ("Dispatched", "Delivery Failed"):
            if st == "Delivery Failed":
                add("Failed Delivery", "Delivery attempt failed - needs immediate follow-up", now)
            if ed and now > ed:
                late = (now - ed).total_seconds() / 86400
                if st == "Dispatched":
                    add("Shipment Delay", f"Expected {o['expected_delivery_date'][:10]}, {late:.1f} days overdue", ed)
                if val > cfg["high_value"]:
                    add("High-Value Overdue", f"Order of Rs {val:,.0f} undelivered {late:.1f} days past expected date", ed)
    # auto-resolve tasks that no longer apply: order moved on, or a changed rule threshold no longer flags it
    for r in c.execute("SELECT e.id,e.order_id,e.exc_type,o.order_status,o.order_date,o.order_value FROM exceptions e "
                       "JOIN orders o ON o.order_id=e.order_id WHERE e.status!='Resolved' AND e.exc_type!='Data Correction'").fetchall():
        why = None
        if r["order_status"] not in STILL_VALID[r["exc_type"]]:
            why = f"order now {r['order_status']}"
        elif r["exc_type"] == "Unconfirmed" and now <= _p(r["order_date"]) + timedelta(hours=cfg["unconfirmed_hours"]):
            why = f"no longer breaches {cfg['unconfirmed_hours']:g}h unconfirmed rule"
        elif r["exc_type"] == "Dispatch Delay" and now <= _p(r["order_date"]) + timedelta(hours=cfg["dispatch_hours"]):
            why = f"no longer breaches {cfg['dispatch_hours']:g}h dispatch rule"
        elif r["exc_type"] == "High-Value Overdue" and r["order_value"] <= cfg["high_value"]:
            why = f"value no longer above Rs {cfg['high_value']:,.0f} threshold"
        if why:
            c.execute("UPDATE exceptions SET status='Resolved' WHERE id=?", (r["id"],))
            audit(c, r["order_id"], r["id"], f"Status changed to Resolved ({why})", now, "System")
    return n


def rerun_rules(now):
    c = conn(); n = run_rules(c, now); c.commit(); c.close()
    return n


def process_upload(df, filename, now, user="Uploader"):
    clean, issues = validate(df)
    c = conn()
    c.executemany("INSERT OR REPLACE INTO orders VALUES(:order_id,:order_date,:customer_name,:phone,:order_value,"
                  ":payment_type,:order_status,:dispatch_date,:expected_delivery_date,:assigned_employee)", clean)
    new_data = 0
    for it in issues:
        new_data += _add_exc(c, f"{it['order_id']}|DATA|{it['problems']}", it["order_id"], it["customer_name"],
                             it["phone"], it["order_value"], it["order_date"], "Data Correction",
                             it["problems"], it["assigned"], now, now)
    c.execute("INSERT INTO uploads(filename,ts,total_rows,valid_rows,issue_rows,user) VALUES(?,?,?,?,?,?)",
              (filename, _f(now), len(df), len(clean), len(issues), user))
    new_rules = run_rules(c, now)
    c.commit(); c.close()
    return dict(total=len(df), valid=len(clean), issues=issues, new_data=new_data, new_rules=new_rules)


# ---------------------------------------------------------------- task actions
def set_status(task_id, new, user, now):
    if new not in TASK_STATUSES:
        raise ValueError("Invalid status")
    c = conn()
    r = c.execute("SELECT order_id,status FROM exceptions WHERE id=?", (task_id,)).fetchone()
    if not r:
        raise ValueError("Task not found")
    if r["status"] != new:
        c.execute("UPDATE exceptions SET status=? WHERE id=?", (new, task_id))
        audit(c, r["order_id"], task_id, f"Status changed {r['status']} -> {new}", now, user)
    c.commit(); c.close()


def reassign(task_id, emp, user, now, reset_deadline=False):
    emp = (emp or "").strip()
    if not emp:
        raise ValueError("Choose an employee")
    c = conn()
    r = c.execute("SELECT order_id,assigned_to,exc_type FROM exceptions WHERE id=?", (task_id,)).fetchone()
    if not r:
        raise ValueError("Task not found")
    if r["assigned_to"] != emp:
        msg = f"Reassigned {r['assigned_to']} -> {emp}"
        if reset_deadline:
            dl = now + timedelta(hours=RULES[r["exc_type"]][1])
            c.execute("UPDATE exceptions SET assigned_to=?, deadline=? WHERE id=?", (emp, _f(dl), task_id))
            msg += f" (deadline reset to {_f(dl)})"
        else:
            c.execute("UPDATE exceptions SET assigned_to=? WHERE id=?", (emp, task_id))
        audit(c, r["order_id"], task_id, msg, now, user)
    c.commit(); c.close()


# ---------------------------------------------------------------- queries / report
def _df(q):
    c = conn(); d = pd.read_sql(q, c); c.close(); return d


def exceptions_df():
    return _df("SELECT * FROM exceptions ORDER BY id")


def audit_df():
    return _df("SELECT * FROM audit ORDER BY id DESC")


def latest_total():
    c = conn(); r = c.execute("SELECT total_rows FROM uploads ORDER BY id DESC LIMIT 1").fetchone(); c.close()
    return r[0] if r else 0


def employees():
    c = conn()
    s = {r[0] for r in c.execute("SELECT assigned_employee FROM orders")} | \
        {r[0] for r in c.execute("SELECT assigned_to FROM exceptions")}
    c.close()
    s.discard("Unassigned"); s.discard(None)
    return sorted(s) or DEFAULT_EMPLOYEES


def with_overdue(df, now):
    df = df.copy()
    df["overdue"] = (pd.to_datetime(df["deadline"]) < pd.Timestamp(now)) & (df["status"] != "Resolved") if len(df) else False
    return df


def build_report(now):
    ex = with_overdue(exceptions_df(), now)
    cols = ["task_id", "order_id", "customer_name", "phone", "exc_type", "reason", "priority", "assigned_to",
            "action", "deadline", "status", "overdue"]
    ex = ex.rename(columns={"id": "task_id"})
    unres = ex[ex["status"] != "Resolved"]
    parts = {"1. New exceptions (today)": ex[pd.to_datetime(ex["created_at"]).dt.date == now.date()] if len(ex) else ex,
             "2. Unresolved exceptions": unres,
             "3. Overdue tasks": unres[unres["overdue"]],
             "4. High-priority cases": unres[unres["priority"] == "High"]}
    out = []
    for name, d in parts.items():
        d = d[cols].copy(); d.insert(0, "section", name); out.append(d)
    if len(unres):
        g = unres.groupby("assigned_to").agg(pending=("task_id", "count"), overdue=("overdue", "sum"),
                                              high=("priority", lambda s: (s == "High").sum())).reset_index()
        e = pd.DataFrame({"section": "5. Employee-wise pending actions", "assigned_to": g["assigned_to"],
                          "reason": g.apply(lambda r: f"{r.pending} pending, {int(r.overdue)} overdue, {r.high} high priority", axis=1)})
        out.append(e)
    return pd.concat(out, ignore_index=True)[["section"] + cols] if out else pd.DataFrame()
