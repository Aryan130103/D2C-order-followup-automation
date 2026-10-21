# D2C Order Follow-up Automation

## Project Demonstration Video
[https://www.loom.com/share/d65bd7a30c45461783a0ea7f259de4a1](https://www.loom.com/share/303e864d23d44813987ad82d5dda711c)

## Tech Stack: 
Python, Streamlit, pandas, SQLite (file `data/app.db`, created automatically).

## Run (works on a fresh machine - nothing assumed to be installed)
1. Install Python 3.10+ from python.org (tick "Add Python to PATH"). Check: `python --version`
2. Open a terminal in this folder and install the libraries:
```
python -m pip install -r requirements.txt
```
3. Start the app (use `python -m`, so it works even if `streamlit` is not on PATH):
```
python -m streamlit run app.py
```
(On Windows, if `python` is not found, use `py` instead: `py -m pip install -r requirements.txt` then `py -m streamlit run app.py`.)

4. The browser opens at http://localhost:8501. Open the **Upload** tab -> "Load bundled sample data" (or upload your own CSV/XLSX).

Optional: `python gen_data.py` regenerates `data/sample_orders.csv` (119 rows, dates relative to now).

## Files
- `app.py` - UI: Dashboard (filters), Upload, Tasks (status / reassign), Daily Report (CSV), Audit Trail
- `engine.py` - validation, 5 rules, task generation, audit, reports (all logic)
- `gen_data.py` - sample data with planted exceptions; `data/sample_orders.csv` - the output

## How it works
- **Validation:** missing mandatory fields, duplicate order IDs, invalid/inconsistent dates, bad status/payment/phone/value -> a *Data Correction* task; such rows are never rule-flagged.
- **Rules (editable in sidebar, Manager only):** unconfirmed > 2h; confirmed not dispatched > 24h; dispatched past expected delivery; failed delivery; value > Rs 5,000 undelivered past expected date. Each creates a task with reason, priority, action, deadline, assignee (from the file), status.
- **No duplicate tasks:** unique key `order_id|rule` in SQLite, `INSERT OR IGNORE` - re-uploading a file creates nothing new.
- **Auto-resolve:** if a later upload shows the order moved on (e.g. Delivered), its tasks resolve.
- **Audit:** every creation, status change and reassignment logged with order ID, action, timestamp, user (sidebar "Acting as").
- **Time:** reference time = now; sidebar lets you override it for demos.

## Assumptions / limitations
- No confirmation timestamp in the schema, so order date is used as the start of both the 2h and 24h clocks.
- Simple user switcher, no real authentication. Single-file SQLite, single instance.

## Before production
- Move SQLite to PostgreSQL (concurrent users, shared server, backups).
- Real login and roles; audit user comes from the session.
- Pull orders from the live system on a schedule, with a real confirmation timestamp.
- Notifications (WhatsApp/email) and escalation for overdue tasks.
- Append-only audit log, protect customer phone data, store times in UTC.

## Problems met and fixed
- ISO dates were read day-first, so valid rows were marked invalid -> parse year-first for ISO.
- `streamlit` not on PATH / missing in venv -> run `python -m pip install -r requirements.txt` and `python -m streamlit run app.py`.
- Raising a rule limit left old tasks open -> re-run now resolves tasks that no longer breach.
- Rule settings needed two saves -> fixed widget keys and rerun after save.
- Upload summary vanished after any change -> kept in session state.
