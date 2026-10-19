import os
import requests
import json
from flask import Flask, request
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()
app = Flask(__name__)

WEBHOOK_URL = os.getenv("WEBHOOK_URL")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "quickfix_verify_123")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
SERVICE_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

# In-memory cache + user state
user_state = {}  # phone -> {step, language, issue, location...}
tenants_cache = {"data": [], "last_fetch": None}
traffic_cache = {}


# ---------- Google Sheets Helpers ----------
def get_sheets_service():
    if not SHEET_ID or not SERVICE_JSON:
        return None
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        creds_json = json.loads(SERVICE_JSON)
        creds = service_account.Credentials.from_service_account_info(
            creds_json, scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        service = build("sheets", "v4", credentials=creds)
        return service
    except Exception as e:
        print(f"Sheets service error: {e}")
        return None


def read_sheet_range(range_name):
    service = get_sheets_service()
    if not service:
        return []
    try:
        result = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=SHEET_ID, range=range_name)
            .execute()
        )
        return result.get("values", [])
    except Exception as e:
        print(f"Read sheet error {range_name}: {e}")
        return []


def append_sheet_range(range_name, values):
    service = get_sheets_service()
    if not service:
        print(f"[MOCK SHEET APPEND] {range_name} -> {values}")
        return
    try:
        service.spreadsheets().values().append(
            spreadsheetId=SHEET_ID,
            range=range_name,
            valueInputOption="USER_ENTERED",
            body={"values": [values]},
        ).execute()
        print(f"Appended to {range_name}")
    except Exception as e:
        print(f"Append error: {e}")


def update_sheet_range(range_name, values):
    service = get_sheets_service()
    if not service:
        return
    try:
        service.spreadsheets().values().update(
            spreadsheetId=SHEET_ID,
            range=range_name,
            valueInputOption="USER_ENTERED",
            body={"values": [values]},
        ).execute()
    except Exception as e:
        print(f"Update error: {e}")


def get_all_tenants():
    # Cache for 2 minutes to avoid API limit
    import time

    now = time.time()
    if (
        tenants_cache["data"]
        and tenants_cache["last_fetch"]
        and now - tenants_cache["last_fetch"] < 120
    ):
        return tenants_cache["data"]

    rows = read_sheet_range("Tenants!A:H")
    if not rows or len(rows) < 2:
        # Fallback if sheet not configured
        return [
            {
                "tenant_id": "PLUMB001",
                "name": "Plumber Lateef",
                "phone": "08030000001",
                "area": "akobo",
                "status": "active",
            },
            {
                "tenant_id": "PLUMB002",
                "name": "Plumber Musa",
                "phone": "08030000002",
                "area": "akobo",
                "status": "active",
            },
        ]

    headers = rows[0]
    tenants = []
    for r in rows[1:]:
        # pad row
        r = r + [""] * (len(headers) - len(r))
        d = dict(zip(headers, r))
        if d.get("status", "").lower() == "active":
            tenants.append(d)

    tenants_cache["data"] = tenants
    tenants_cache["last_fetch"] = now
    return tenants


def get_traffic_row(area):
    rows = read_sheet_range("Traffic_Distribution!A:F")
    if not rows or len(rows) < 2:
        return {"area": area, "last_index": 0, "row_number": None}
    headers = rows[0]
    for idx, r in enumerate(rows[1:], start=2):  # row_number is 1-indexed in sheets
        r = r + [""] * (len(headers) - len(r))
        d = dict(zip(headers, r))
        if d.get("area", "").lower() == area.lower():
            d["row_number"] = idx
            try:
                d["last_index"] = int(d.get("last_index", 0) or 0)
            except:
                d["last_index"] = 0
            return d
    # fallback default
    return {"area": "default", "last_index": 0, "row_number": None, "plumber_ids": ""}


def assign_plumber_sheet_based(customer_location_text):
    loc = customer_location_text.lower()
    tenants = get_all_tenants()

    # Find area: check if location text contains tenant area
    # Priority: find matching area from Traffic_Distribution
    traffic_rows = read_sheet_range("Traffic_Distribution!A:F")
    areas = []
    if traffic_rows and len(traffic_rows) > 1:
        for r in traffic_rows[1:]:
            if r:
                areas.append(r[0].lower())

    detected_area = "default"
    for area in areas:
        if area != "default" and area in loc:
            detected_area = area
            break

    # Filter tenants for this area
    area_tenants = [t for t in tenants if t.get("area", "").lower() == detected_area]
    if not area_tenants:
        area_tenants = [
            t for t in tenants if t.get("area", "").lower() == "akobo"
        ] or tenants  # fallback

    if not area_tenants:
        return {
            "area": detected_area,
            "plumber": {
                "tenant_id": "PLUMB001",
                "name": "Plumber Lateef",
                "phone": "08030000001",
            },
            "next_index": 1,
        }

    traffic = get_traffic_row(detected_area)
    last_idx = traffic.get("last_index", 0)

    # Round robin
    plumber_data = area_tenants[last_idx % len(area_tenants)]
    next_idx = (last_idx + 1) % len(area_tenants)

    # Update traffic sheet last_index
    if traffic.get("row_number"):
        try:
            # Update last_index column D (index 3) and total_today E
            # Traffic_Distribution: A area, B plumber_ids, C plumber_names, D last_index, E total_today, F total_all_time
            # We update D and E
            service = get_sheets_service()
            if service:
                # Read current total
                current_total = 0
                try:
                    current_total = int(traffic.get("total_today", 0) or 0)
                except:
                    current_total = 0
                # Update range D{row}:F{row}
                range_to_update = f"Traffic_Distribution!D{traffic['row_number']}:F{traffic['row_number']}"
                service.spreadsheets().values().update(
                    spreadsheetId=SHEET_ID,
                    range=range_to_update,
                    valueInputOption="USER_ENTERED",
                    body={
                        "values": [
                            [
                                str(next_idx),
                                str(current_total + 1),
                                str(int(traffic.get("total_all_time", 0) or 0) + 1),
                            ]
                        ]
                    },
                ).execute()
                print(f"Updated traffic {detected_area} last_index -> {next_idx}")
        except Exception as e:
            print(f"Failed to update traffic: {e}")

    # Also update tenant total_jobs
    try:
        # Find tenant row and increment total_jobs
        tenant_rows = read_sheet_range("Tenants!A:H")
        if tenant_rows:
            headers = tenant_rows[0]
            tid_idx = headers.index("tenant_id") if "tenant_id" in headers else 0
            jobs_idx = headers.index("total_jobs") if "total_jobs" in headers else 6
            for idx, r in enumerate(tenant_rows[1:], start=2):
                if len(r) > tid_idx and r[tid_idx] == plumber_data.get("tenant_id"):
                    try:
                        current_jobs = int(r[jobs_idx] or 0) if len(r) > jobs_idx else 0
                        service = get_sheets_service()
                        if service:
                            col_letter = chr(65 + jobs_idx)  # A=0
                            service.spreadsheets().values().update(
                                spreadsheetId=SHEET_ID,
                                range=f"Tenants!{col_letter}{idx}",
                                valueInputOption="USER_ENTERED",
                                body={"values": [[str(current_jobs + 1)]]},
                            ).execute()
                    except Exception as e:
                        print(f"Tenant update error: {e}")
                    break
    except Exception as e:
        print(f"Tenant job count error: {e}")

    return {
        "area": detected_area,
        "plumber": plumber_data,
        "next_index": next_idx,
        "all_area_tenants": area_tenants,
    }


# ---------- WhatsApp Send ----------
def send_whatsapp(to, text):
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
        print(f"[MOCK SEND to {to}]: {text}")
        return
    url = f"https://graph.facebook.com/v20.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    data = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text},
    }
    try:
        r = requests.post(url, headers=headers, json=data, timeout=10)
        print(f"Sent to {to} status {r.status_code}")
    except Exception as e:
        print(f"Send error: {e}")


# ---------- Flask Routes ----------
@app.route("/")
def home():
    return "Welcome to QuickFix Bot V2"


@app.route("/webhook", methods=["GET"])
def verify():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        print("Webhook verified")
        return challenge, 200
    return "Forbidden", 403


@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    try:
        entry = data["entry"][0]
        change = entry["changes"][0]
        value = change["value"]
        messages = value.get("messages")
        if not messages:
            return "OK", 200
        msg = messages[0]
        from_number = msg["from"]
        text = msg.get("text", {}).get("body", "").strip()
        contact_name = value.get("contacts", [{}])[0].get("profile", {}).get("name", "")

        print(f"From {from_number}: {text}")

        if from_number not in user_state:
            user_state[from_number] = {"step": "LANGUAGE"}

        state = user_state[from_number]

        if text.upper() == "PAID":
            # Update Leads_CRM paid=Yes
            if SHEET_ID:
                # Find last lead for this phone and update - simplified: just log
                print(f"PAID received from {from_number}")
                # You can implement search and update here
            send_whatsapp(
                from_number,
                f"Payment confirmed! ✅\n\n{state.get('plumber_name', 'Plumber Lateef')} will call you now: {state.get('plumber_phone', '08138363768')}\nYour job code: {state.get('job_code', 'JOB')}\n\nIf e no call in 10 mins, call {state.get('plumber_phone', '08138363768')}",
            )
            user_state[from_number] = {"step": "LANGUAGE"}
            return "OK", 200

        if state["step"] == "LANGUAGE":
            send_whatsapp(
                from_number,
                "Welcome to QuickFix Plumbers Ibadan! 🔧\nWe dey fix pipe sharp sharp.\n\nChoose language:\n1 - English\n2 - Pidgin\n\nReply 1 or 2",
            )
            state["step"] = "WAIT_LANGUAGE"
            return "OK", 200

        if state["step"] == "WAIT_LANGUAGE":
            if text in ["1", "2"]:
                state["language"] = "en" if text == "1" else "pidgin"
                state["step"] = "ISSUE"
                q = (
                    "What is the issue?\n1 - Leaking pipe\n2 - Blocked toilet/sink\n3 - Install new tap/pipe\n4 - Emergency\n\nReply 1-4"
                    if state["language"] == "en"
                    else "Wetin spoil?\n1 - Water dey leak\n2 - Toilet or sink block\n3 - Make we install new tap/pipe\n4 - Emergency now now\n\nReply 1-4"
                )
                send_whatsapp(from_number, q)
            else:
                send_whatsapp(
                    from_number, "Abeg reply 1 or 2 only.\n1 - English\n2 - Pidgin"
                )
            return "OK", 200

        if state["step"] == "ISSUE":
            state["issue"] = text
            state["step"] = "LOCATION"
            q = "Where you dey? Send full address + area.\nExample: No 5 Akobo Road, Ibadan"
            send_whatsapp(from_number, q)
            return "OK", 200

        if state["step"] == "LOCATION":
            state["location"] = text
            # V2 Sheet-based assignment - NO CODE EDIT
            assignment = assign_plumber_sheet_based(text)
            plumber = assignment["plumber"]
            area = assignment["area"]

            state["plumber_name"] = plumber.get("name", "Plumber Lateef")
            state["plumber_phone"] = plumber.get("phone", "08138363768")
            state["plumber_id"] = plumber.get("tenant_id", "PLUMB001")
            state["area"] = area
            state["job_code"] = (
                f"JOB-{from_number[-4:]}-{datetime.now().strftime('%H%M')}"
            )

            # Save to Leads_CRM - FULL CRM
            job_data = [
                datetime.now().isoformat(),  # date
                from_number,  # customer_phone
                contact_name,  # customer_name
                text,  # location
                state["issue"],  # issue_type
                area,  # area_detected
                state["plumber_id"],  # assigned_plumber_id
                state["plumber_name"],  # assigned_plumber
                state["plumber_phone"],  # assigned_phone
                "No",  # paid
                "New Lead",  # status
                state["job_code"],  # job_code
                "",  # paystack_ref
            ]
            append_sheet_range("Leads_CRM!A:M", job_data)

            pay_link = "https://paystack.com/pay/quickfix-plumber-2000"
            if state["language"] == "en":
                msg = f"Thanks! ✅\n\nIssue: {state['issue']}\nLocation: {text}\nArea: {area}\nAssigned: {state['plumber_name']} - {state['plumber_phone']}\nJob Code: {state['job_code']}\nHe will come in 1 hour.\n\nCall-out fee: N2,000 (remove from work fee).\n\nPay here:\n{pay_link}\n\nAfter payment type PAID"
            else:
                msg = f"Ok, {state['plumber_name']} dey near {text} ({area}). E go reach you in 1 hour.\n\nJob Code: {state['job_code']}\nMoney to lock booking: N2,000 (We go comot am from work money).\n\nPay here:\n{pay_link}\n\nAfter you pay type PAID"
            send_whatsapp(from_number, msg)
            state["step"] = "WAIT_PAID"
            return "OK", 200

        if state["step"] == "WAIT_PAID":
            send_whatsapp(
                from_number,
                "If you have paid, type PAID. If not, pay here: https://paystack.com/pay/quickfix-plumber-2000",
            )

    except Exception as e:
        print(f"Webhook error: {e}")
        import traceback

        traceback.print_exc()

    return "OK", 200


if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
