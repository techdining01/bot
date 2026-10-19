
# V2 Sheet-Based Bot - NEVER EDIT CODE AGAIN

## What's new in V2?
- Plumbers list comes from Google Sheet Tenants tab, NOT from app.py line 18
- Round robin counter stored in Traffic_Distribution tab, survives redeploy
- Lead CRM auto-saves to Leads_CRM with job_code, paid status
- Add new plumber = Add row in sheet only

## Google Sheet Structure (V2_Plumber_MultiTenant_Sheet.xlsx)
- Tenants: tenant_id, name, phone, area, skills, status(active/inactive), total_jobs, joined_date
- Traffic_Distribution: area, plumber_ids, plumber_names, last_index, total_today, total_all_time
- Leads_CRM: date, customer_phone, customer_name, location, issue_type, area_detected, assigned_plumber_id, assigned_plumber, assigned_phone, paid, status, job_code, paystack_ref

## How to Add New Plumber WITHOUT code - 30 seconds
1. Open your Google Sheet (the V2 sheet you uploaded)
2. Go to Tenants tab -> Add new row at bottom:
   tenant_id: PLUMB005
   name: Plumber Tunde
   phone: 08030000005
   area: akobo
   skills: All
   status: active
   total_jobs: 0
   joined_date: 2026-10-17

3. Go to Traffic_Distribution tab -> Find area akobo row
   Edit plumber_ids: PLUMB001,PLUMB002,PLUMB005
   Edit plumber_names: Plumber Lateef, Plumber Musa, Plumber Tunde

4. DONE! Next customer in akobo will go Lateef -> Musa -> Tunde -> Lateef...

No deploy, no code edit, no overwrite.

## To Add New Area (e.g., Sango)
1. Tenants tab: Add PLUMB006 with area sango
2. Traffic_Distribution: Add new row: area sango, plumber_ids PLUMB006, plumber_names Plumber XYZ, last_index 0, total_today 0, total_all_time 0

Bot will now detect "Sango" in customer location and assign.

## Deploy (Same as V1)
- Render.com: Build pip install -r requirements_v2.txt, Start gunicorn app_v2:app
- Env vars: VERIFY_TOKEN, WHATSAPP_TOKEN, PHONE_NUMBER_ID, GOOGLE_SHEET_ID, GOOGLE_SERVICE_ACCOUNT_JSON
- Webhook: https://your-app.onrender.com/webhook

## Cost
$0/month - Sheet free, Render free, Meta 1000 conversations free.

## Deactivating Plumber
Set status to inactive in Tenants tab - bot will skip him automatically.
