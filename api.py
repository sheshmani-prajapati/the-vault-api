from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import json
from pydantic import BaseModel
from typing import Optional
from supabase import create_client, Client
import csv
import os
import re
import traceback

app = FastAPI(title="The Vault API - v3.5 (With Persistent Supabase)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- SUPABASE DATABASE SETUP ---
# We use environment variables so your API keys aren't exposed in Github
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase: Client | None = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        print("✅ Supabase successfully connected.")
    except Exception as e:
        print("❌ Failed to initialize Supabase:", str(e))
else:
    print("⚠️ Warning: Supabase keys not found in environment variables.")

# --- TRACKING DATA MODEL ---
class TrackingPayload(BaseModel):
    event: str
    product_id: Optional[str] = None
    brand: Optional[str] = None
    result: Optional[str] = None
    uuid: Optional[str] = "anonymous" # Added the UUID from the Chrome Extension

# --- NEW TRACKING ENDPOINT ---
@app.post("/track_event")
async def track_event(payload: TrackingPayload):
    if not supabase:
        print("Warning: Event received but Supabase is not connected.")
        return {"status": "error", "message": "Database not configured"}
        
    try:
        # 1. Upsert the User (Creates them if they are new, ignores if they exist)
        supabase.table("vault_users").upsert({
            "uuid": payload.uuid
        }).execute()

        # 2. Log the exact action they took to the tracking table
        supabase.table("tracking_events").insert({
            "user_uuid": payload.uuid,
            "brand_name": payload.brand or "Unknown Brand",
            "event_type": payload.event,
            "product_id": payload.product_id or "N/A",
            "result_text": payload.result or "N/A"
        }).execute()
        
        return {"status": "success", "recorded_event": payload.event}
        
    except Exception as e:
        # We catch the error silently so it NEVER crashes the Shopify cart
        print(f"Vault Tracking Error: {str(e)}")
        return {"status": "error", "message": "Failed to log event"}

# --- HELPER FUNCTIONS ---
def safe_float(value):
    try:
        return float(value)
    except (ValueError, TypeError, AttributeError):
        return None

def safe_str(value):
    if value is None:
        return ""
    return str(value).strip()

def normalize_match(value):
    if not value:
        return ""
    val = str(value).lower().replace("'", "").strip()
    if val.endswith(" fit"):
        val = val[:-4].strip()
    return val

# --- EXISTING ENDPOINTS ---
@app.get("/get_fit")
def check_fit(ref_brand: str, ref_size: str, ref_fit: str, target_brand: str, target_fit: str = "Regular Fit"):
    try:
        db = []
        
        # 1. SMART FILE LOCATOR
        file_path = "vault_tshirt_database.csv"
        if not os.path.exists(file_path):
            for f in os.listdir("."):
                if f.lower() == "vault_tshirt_database.csv":
                    file_path = f
                    break

        if not os.path.exists(file_path):
            raise ValueError(f"Could not find the database file on Render.")

        # 2. SAFE FILE READER
        try:
            with open(file_path, mode='r', encoding='utf-8-sig') as file:
                reader = csv.DictReader(file)
                for row in reader:
                    db.append(row)
        except UnicodeDecodeError:
            with open(file_path, mode='r', encoding='latin1') as file:
                reader = csv.DictReader(file)
                for row in reader:
                    db.append(row)

        if not db:
            raise ValueError("The CSV file was found, but it appears to be empty.")

        norm_ref_brand = normalize_match(ref_brand)
        norm_ref_fit = normalize_match(ref_fit)
        norm_target_brand = normalize_match(target_brand)
        norm_target_fit = normalize_match(target_fit)
        norm_ref_size = safe_str(ref_size).upper()

        # FIND ANCHOR
        ref_data = None
        exact_fit_found = True
        
        for row in db:
            if (normalize_match(row.get('Brand')) == norm_ref_brand and 
                safe_str(row.get('Size Label')).upper() == norm_ref_size and 
                normalize_match(row.get('Fit Type')) == norm_ref_fit):
                ref_data = row
                break
                
        if not ref_data:
            for row in db:
                if (normalize_match(row.get('Brand')) == norm_ref_brand and 
                    safe_str(row.get('Size Label')).upper() == norm_ref_size):
                    ref_data = row
                    exact_fit_found = False
                    break
                    
        if not ref_data:
            raise ValueError(f"We don't have the exact data for {ref_brand.title()} {ref_size.upper()} yet.")

        min_chest = safe_float(ref_data.get('Chest Min (Inches)'))
        max_chest = safe_float(ref_data.get('Chest Max (Inches)'))
        if min_chest is None or max_chest is None:
            raise ValueError("Anchor chest data missing in CSV. We need numbers to do the math!")
            
        ref_true_inches = (min_chest + max_chest) / 2
        ref_fit_type = safe_str(ref_data.get('Fit Type')).lower()
        ref_shoulder = safe_float(ref_data.get('Shoulder (Inches)'))
        
        # FIND TARGET
        best_match = None
        smallest_penalty_score = 9999

        for row in db:
            t_brand_match = normalize_match(row.get('Brand')) == norm_target_brand
            t_fit_match = normalize_match(row.get('Fit Type')) == norm_target_fit

            if t_brand_match and t_fit_match:
                t_min = safe_float(row.get('Chest Min (Inches)'))
                t_max = safe_float(row.get('Chest Max (Inches)'))
                
                if t_min is None or t_max is None: 
                    continue
                
                target_true_inches = (t_min + t_max) / 2
                target_shoulder = safe_float(row.get('Shoulder (Inches)'))
                
                chest_diff = target_true_inches - ref_true_inches
                
                if chest_diff < 0:
                    chest_score = abs(chest_diff) * 3.0 
                else:
                    chest_score = chest_diff

                shoulder_score = 0
                if ref_shoulder is not None and target_shoulder is not None:
                    shoulder_diff = target_shoulder - ref_shoulder
                    if shoulder_diff < -0.5: 
                        shoulder_score = abs(shoulder_diff) * 2.0
                    else:
                        shoulder_score = abs(shoulder_diff) * 0.5 
                
                total_score = chest_score + shoulder_score

                if total_score < smallest_penalty_score:
                    smallest_penalty_score = total_score
                    best_match = row

        if not best_match:
            raise ValueError(f"Could not calculate sizes for {target_brand.title()} {target_fit.title()}.")

        # DEALBREAKER
        winner_t_min = safe_float(best_match.get('Chest Min (Inches)'))
        winner_t_max = safe_float(best_match.get('Chest Max (Inches)'))
        winner_target_inches = (winner_t_min + winner_t_max) / 2
        
        final_chest_diff = winner_target_inches - ref_true_inches
        
        if final_chest_diff < -1.5:
            raise ValueError(f"{target_brand.title()} does not manufacture a size large enough to match your {ref_brand.title()} {ref_size}.")

        # WARNINGS
        target_fit_type = safe_str(best_match.get('Fit Type')).lower()
        warning_message = "✨ Perfect Fit: The chest and style match your usual size beautifully."

        t_shoulder_val = safe_float(best_match.get('Shoulder (Inches)'))
        
        if ref_shoulder and t_shoulder_val:
            s_diff = t_shoulder_val - ref_shoulder
            if s_diff > 2.0:
                warning_message = f"👕 Style Note: This fits your chest, but features a trendy drop-shoulder. It will look intentionally baggier than your {ref_brand.title()} tee."
            elif s_diff < -1.0:
                warning_message = f"⚠️ Style Note: This fits your chest, but the shoulders are cut narrower. It might feel slightly snug."
            elif ref_fit_type in ["regular", "slim"] and target_fit_type in ["oversized", "boxy", "loose", "relaxed"]:
                warning_message = f"👕 Style Note: This fits your chest, but {target_brand.title()} cuts this specific shirt to be intentionally oversized."
        elif ref_fit_type in ["regular", "slim"] and target_fit_type in ["oversized", "boxy", "loose", "relaxed"]:
            warning_message = f"👕 Style Note: This fits your chest, but {target_brand.title()} designed this to be a baggy fit."
        elif ref_fit_type in ["oversized", "boxy", "loose", "relaxed"] and target_fit_type in ["slim", "regular"]:
            warning_message = f"⚠️ Style Note: This matches your measurements, but it's a slimmer cut. It will hug your body tighter than you are used to."

        if not exact_fit_found:
            fallback_label = safe_str(ref_data.get('Fit Type')).title()
            warning_message = f"💡 We based this on your {ref_brand.title()} ({fallback_label}) size. " + warning_message

        return {
            "status": "success",
            "target_brand": target_brand.title(),
            "recommended_size": safe_str(best_match.get('Size Label')).upper(),
            "warning": warning_message
        }

    except Exception as e:
        print("CRASH LOG:", traceback.format_exc())
        raise HTTPException(status_code=400, detail=f"Engine Error: {str(e)}")

@app.get("/meta")
def get_metadata():
    brand_data = {}
    try:
        file_path = "vault_tshirt_database.csv"
        if not os.path.exists(file_path):
            for f in os.listdir("."):
                if f.lower() == "vault_tshirt_database.csv":
                    file_path = f
                    break
                    
        with open(file_path, mode='r', encoding='utf-8-sig') as file:
            reader = csv.DictReader(file)
            for row in reader:
                brand = safe_str(row.get('Brand')).title()
                fit = safe_str(row.get('Fit Type')).title()
                size = safe_str(row.get('Size Label')).upper()
                
                if not brand or not fit or not size:
                    continue
                    
                if brand not in brand_data:
                    brand_data[brand] = {}
                if fit not in brand_data[brand]:
                    brand_data[brand][fit] = []
                if size not in brand_data[brand][fit]:
                    brand_data[brand][fit].append(size)
    except Exception:
        pass
        
    return brand_data
    # --- B2B ENTERPRISE DASHBOARD ---
@app.get("/dashboard", response_class=HTMLResponse)
async def b2b_dashboard(brand: str = "Snitch"):
    if not supabase:
        return "<h1>Dashboard Offline: Supabase not connected.</h1>"

    try:
        res = supabase.table("tracking_events").select("*").ilike("brand_name", brand).execute()
        events = res.data

        funnel = {"widget_opened": 0, "size_calculated": 0, "add_to_cart_intent": 0, "purchase_completed": 0}
        feedback = {"Perfect": 0, "Too Tight": 0, "Too Loose": 0, "Returned": 0}
        brand_counts = {}
        sku_counts = {} # NEW: Dictionary to track SKU popularity

        for e in events:
            evt = e.get("event_type")
            res_text = e.get("result_text", "")
            product_url = e.get("product_id", "")
            
            if evt in funnel:
                funnel[evt] += 1
            
            if evt == "verified_fit_feedback":
                clean_feedback = res_text.replace("User reported: ", "").strip()
                if clean_feedback in feedback:
                    feedback[clean_feedback] += 1
                else:
                    feedback[clean_feedback] = 1

            if evt == "size_calculated":
                match = re.search(r'\(from (.+?)\)', res_text)
                if match:
                    ref_b = match.group(1).upper()
                    brand_counts[ref_b] = brand_counts.get(ref_b, 0) + 1

            # NEW: SKU/Product Parsing
            if evt in ["size_calculated", "add_to_cart_intent"] and "/" in product_url:
                # Clean up the Shopify URL (e.g., /men-t-shirts/black-shirt -> Black Shirt)
                clean_sku = product_url.split("/")[-1].split("?")[0].replace("-", " ").title()
                if len(clean_sku) > 3: # Ignore weird tiny fragments
                    sku_counts[clean_sku] = sku_counts.get(clean_sku, 0) + 1

        # Format Reference Brands
        total_refs = sum(brand_counts.values())
        reference_brands = []
        if total_refs > 0:
            for b, count in sorted(brand_counts.items(), key=lambda x: x[1], reverse=True)[:5]:
                reference_brands.append({"brand": b, "percentage": round((count/total_refs)*100, 1), "count": count})

        # Format Top SKUs into HTML Table Rows
        sku_table_rows = ""
        for sku, count in sorted(sku_counts.items(), key=lambda x: x[1], reverse=True)[:6]:
            sku_table_rows += f"""
            <tr class="border-b border-slate-700/50 hover:bg-slate-800/30 transition-colors">
                <td class="py-3 px-4 text-slate-300 text-sm font-medium">{sku}</td>
                <td class="py-3 px-4 text-right"><span class="bg-indigo-500/20 text-indigo-300 py-1 px-3 rounded-full text-xs font-bold">{count} Interactions</span></td>
            </tr>
            """
        if not sku_table_rows:
            sku_table_rows = "<tr><td colspan='2' class='py-4 text-center text-slate-500 text-sm'>No product data captured yet.</td></tr>"

        vault_opens = funnel["widget_opened"]
        purchases = funnel["purchase_completed"]
        cvr = round((purchases / vault_opens) * 100, 1) if vault_opens > 0 else 0.0
        rto_saved = purchases - feedback.get("Returned", 0)

        dashboard_data = {
            "brand_name": brand.upper(),
            "kpis": {"vault_conversion_rate": cvr, "rto_saved_count": rto_saved, "add_to_bag": funnel["add_to_cart_intent"]},
            "funnel": funnel,
            "reference_brands": reference_brands,
            "fit_feedback": {"perfect": feedback.get("Perfect", 0), "too_tight": feedback.get("Too Tight", 0), "too_loose": feedback.get("Too Loose", 0), "returned": feedback.get("Returned", 0)},
        }

        html_content = f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>The Vault | Enterprise Intelligence</title>
            <script src="https://cdn.tailwindcss.com"></script>
            <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
            <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
            <style> body {{ font-family: 'Inter', sans-serif; background-color: #0f172a; color: #f8fafc; }} .glass-card {{ background: rgba(30, 41, 59, 0.7); border: 1px solid rgba(51, 65, 85, 0.8); backdrop-filter: blur(12px); }} </style>
        </head>
        <body class="p-6 md:p-10">
            <div class="max-w-7xl mx-auto">
                <div class="flex justify-between items-end mb-10 border-b border-slate-800 pb-6">
                    <div>
                        <div class="flex items-center gap-3 mb-2">
                            <div class="h-3 w-3 rounded-full bg-emerald-500 animate-pulse"></div>
                            <span class="text-emerald-500 text-xs font-bold tracking-widest uppercase">Live Connection</span>
                        </div>
                        <h1 class="text-4xl font-bold tracking-tight text-white">{dashboard_data['brand_name']} Intelligence</h1>
                    </div>
                </div>

                <div class="grid grid-cols-1 md:grid-cols-3 gap-6 mb-10">
                    <div class="glass-card rounded-2xl p-6">
                        <h3 class="text-slate-400 text-sm font-semibold uppercase tracking-wider mb-2">Vault Conversion Rate</h3>
                        <div class="flex items-baseline gap-2"><span class="text-5xl font-bold text-white">{dashboard_data['kpis']['vault_conversion_rate']}%</span></div>
                    </div>
                    <div class="glass-card rounded-2xl p-6">
                        <h3 class="text-slate-400 text-sm font-semibold uppercase tracking-wider mb-2">High-Intent Add to Bags</h3>
                        <div class="flex items-baseline gap-2"><span class="text-5xl font-bold text-indigo-400">{dashboard_data['kpis']['add_to_bag']}</span></div>
                    </div>
                    <div class="glass-card rounded-2xl p-6 border-t-4 border-t-emerald-500">
                        <h3 class="text-slate-400 text-sm font-semibold uppercase tracking-wider mb-2">Est. RTO Disasters Prevented</h3>
                        <div class="flex items-baseline gap-2"><span class="text-5xl font-bold text-emerald-400">{dashboard_data['kpis']['rto_saved_count']}</span><span class="text-slate-500 text-sm font-medium">Orders</span></div>
                    </div>
                </div>

                <div class="grid grid-cols-1 lg:grid-cols-2 gap-8 mb-8">
                    <div class="glass-card p-6 rounded-2xl">
                        <h3 class="text-lg font-semibold text-white mb-6">The Intent Funnel</h3>
                        <canvas id="funnelChart" height="220"></canvas>
                    </div>
                    <div class="glass-card p-6 rounded-2xl">
                        <h3 class="text-lg font-semibold text-white mb-6">Market Share Anchors (What they wear)</h3>
                        <canvas id="competitorChart" height="220"></canvas>
                    </div>
                </div>

                <div class="grid grid-cols-1 lg:grid-cols-3 gap-8 mb-8">
                    <div class="glass-card p-6 rounded-2xl col-span-1">
                        <h3 class="text-lg font-semibold text-white mb-6">Post-Purchase Fit Verification</h3>
                        <div class="relative h-64"><canvas id="feedbackChart"></canvas></div>
                    </div>
                    
                    <div class="glass-card p-0 rounded-2xl col-span-2 overflow-hidden flex flex-col">
                        <div class="p-6 border-b border-slate-700/50 bg-slate-800/30">
                            <h3 class="text-lg font-semibold text-white">Top Merchandising Friction Points</h3>
                            <p class="text-slate-400 text-sm mt-1">SKUs causing the highest sizing confusion among shoppers.</p>
                        </div>
                        <div class="p-0 overflow-auto flex-grow">
                            <table class="w-full text-left border-collapse">
                                <thead>
                                    <tr class="bg-slate-900/50">
                                        <th class="py-3 px-6 text-xs font-semibold text-slate-400 uppercase tracking-wider">Product Name (SKU)</th>
                                        <th class="py-3 px-6 text-xs font-semibold text-slate-400 uppercase tracking-wider text-right">Vault Usage</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {sku_table_rows}
                                </tbody>
                            </table>
                        </div>
                    </div>
                </div>
            </div>

            <script>
                const DATA = {json.dumps(dashboard_data)};
                Chart.defaults.color = '#94a3b8';
                Chart.defaults.font.family = 'Inter';

                new Chart(document.getElementById('funnelChart'), {{ type: 'bar', data: {{ labels: ['Opened Widget', 'Calculated Size', 'Added to Bag', 'Purchased'], datasets: [{{ data: [DATA.funnel.widget_opened, DATA.funnel.size_calculated, DATA.funnel.add_to_cart_intent, DATA.funnel.purchase_completed], backgroundColor: ['#334155', '#475569', '#6366f1', '#10b981'], borderRadius: 8, barThickness: 40 }}] }}, options: {{ plugins: {{ legend: {{ display: false }} }} }} }});

                new Chart(document.getElementById('competitorChart'), {{ type: 'bar', data: {{ labels: DATA.reference_brands.map(b => b.brand), datasets: [{{ data: DATA.reference_brands.map(b => b.count), backgroundColor: '#8b5cf6', borderRadius: 6 }}] }}, options: {{ indexAxis: 'y', plugins: {{ legend: {{ display: false }} }} }} }});

                new Chart(document.getElementById('feedbackChart'), {{ type: 'doughnut', data: {{ labels: ['Perfect', 'Too Tight', 'Too Loose', 'Returned'], datasets: [{{ data: [DATA.fit_feedback.perfect, DATA.fit_feedback.too_tight, DATA.fit_feedback.too_loose, DATA.fit_feedback.returned], backgroundColor: ['#10b981', '#f59e0b', '#3b82f6', '#ef4444'], borderWidth: 0, cutout: '75%' }}] }}, options: {{ maintainAspectRatio: false, plugins: {{ legend: {{ position: 'bottom' }} }} }} }});
            </script>
        </body>
        </html>
        """
        return HTMLResponse(content=html_content, status_code=200)

    except Exception as e:
        return f"<h1 style='color:white; background:black; padding:20px;'>Dashboard Error: {str(e)}</h1>"
