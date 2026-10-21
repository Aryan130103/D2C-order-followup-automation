"""Generates data/sample_orders.csv (~118 rows) relative to the current time, with planted exceptions."""
import os, random
from datetime import datetime, timedelta
import pandas as pd

random.seed(42)
now = datetime.now().replace(second=0, microsecond=0)
EMP = ["Anita", "Rahul", "Priya", "Suresh", "Meera"]
NAMES = ["Arjun Nair", "Divya Menon", "Karthik Iyer", "Sneha Reddy", "Vikram Singh", "Pooja Sharma", "Rohit Verma",
         "Neha Gupta", "Amit Patel", "Lakshmi Pillar", "Manoj Kumar", "Fatima Khan", "Ravi Shankar", "Isha Joshi", "Joseph Mathew"]
F = "%Y-%m-%d %H:%M"
h = lambda x: timedelta(hours=x)
d_ = lambda x: timedelta(days=x)
cnt = [1000]


def row(status, od, disp=None, exp=None, value=None, **kw):
    cnt[0] += 1
    r = dict(order_id=f"NX{cnt[0]}", order_date=od.strftime(F), customer_name=random.choice(NAMES),
             phone="9" + "".join(random.choice("0123456789") for _ in range(9)),
             order_value=value or random.randint(499, 4500), payment_type=random.choice(["Prepaid", "COD"]),
             order_status=status, dispatch_date=disp.strftime(F) if disp else "",
             expected_delivery_date=exp.strftime(F) if exp else "", assigned_employee=random.choice(EMP))
    r.update(kw)
    return r


rows = []
# ---- normal orders
for _ in range(30):
    od = now - h(random.randint(72, 240)); d = od + h(random.randint(4, 20)); rows.append(row("Delivered", od, d, d + d_(4)))
for _ in range(10):
    od = now - h(random.randint(24, 48)); d = od + h(random.randint(3, 10)); rows.append(row("Dispatched", od, d, now + d_(random.randint(1, 4))))
for _ in range(2):  # high value but on time -> must NOT be flagged
    od = now - h(30); d = od + h(5); rows.append(row("Dispatched", od, d, now + d_(2), value=9000))
for _ in range(8):
    rows.append(row("Confirmed", now - h(random.randint(1, 20))))
for _ in range(6):
    rows.append(row("Unconfirmed", now - timedelta(minutes=random.randint(5, 100))))
for _ in range(4):
    rows.append(row("Cancelled", now - h(random.randint(5, 100))))
# ---- planted exceptions
for _ in range(10):  # unconfirmed > 2h
    rows.append(row("Unconfirmed", now - h(random.randint(3, 30))))
for _ in range(10):  # confirmed, not dispatched > 24h
    rows.append(row("Confirmed", now - h(random.randint(26, 80))))
for _ in range(10):  # shipment delay (low value)
    od = now - d_(random.randint(5, 9)); rows.append(row("Dispatched", od, od + h(8), now - h(random.randint(6, 100)), value=random.randint(600, 4500)))
for _ in range(8):  # failed delivery
    od = now - d_(random.randint(4, 7)); rows.append(row("Delivery Failed", od, od + h(8), now - h(random.randint(2, 50))))
for _ in range(8):  # high-value overdue
    od = now - d_(random.randint(5, 9)); rows.append(row("Dispatched", od, od + h(8), now - h(random.randint(6, 100)), value=random.randint(5200, 18000)))
random.shuffle(rows)
# ---- bad records
od = now - h(10)
rows += [random.choice(rows[:50]).copy() for _ in range(4)]                       # duplicate order IDs
rows.append(row("Confirmed", od, customer_name=""))                                # missing customer
rows.append(row("Unconfirmed", od, phone=""))                                      # missing phone
rows.append(row("Confirmed", od, assigned_employee=""))                            # missing employee
rows.append(row("Dispatched", od, None, now + d_(2)))                              # dispatched, no dispatch date
rows.append(row("Confirmed", od, order_date="31-02-2026 10:00"))                   # impossible date
rows.append(row("Confirmed", od, order_date="not a date"))                         # garbage date
rows.append(row("Dispatched", od, od + h(3), od - d_(1)))                          # expected before order date
rows.append(row("Shipped", od))                                                    # invalid status
rows.append(row("Confirmed", od, phone="12345"))                                   # invalid phone

os.makedirs("data", exist_ok=True)
pd.DataFrame(rows).to_csv("data/sample_orders.csv", index=False)
print(f"Wrote data/sample_orders.csv with {len(rows)} rows")
