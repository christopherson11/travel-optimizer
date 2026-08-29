
import re
import requests
import streamlit as st
from datetime import date
from uuid import uuid4

st.set_page_config(page_title="Travel Optimizer", page_icon="✈️", layout="wide")
API_BASE = "https://api.liteapi.travel/v3.0"

def api_get(path, key, params=None):
    r = requests.get(API_BASE + path, params=params or {},
                     headers={"X-API-Key": key, "Accept": "application/json"}, timeout=20)
    if r.status_code == 401:
        raise RuntimeError("Nuitee rejected the API key (401). Use the sandbox key beginning with sand_.")
    if not r.ok:
        try: detail = r.json()
        except Exception: detail = r.text
        raise RuntimeError(f"Nuitee returned HTTP {r.status_code}: {detail}")
    return r.json()

def api_post(path, payload, key):
    r = requests.post(API_BASE + path, json=payload,
                      headers={"X-API-Key": key, "Content-Type": "application/json",
                               "Accept": "application/json"}, timeout=35)
    if r.status_code == 401:
        raise RuntimeError("Nuitee rejected the API key (401). Use the sandbox key beginning with sand_.")
    if not r.ok:
        try: detail = r.json()
        except Exception: detail = r.text
        raise RuntimeError(f"Nuitee returned HTTP {r.status_code}: {detail}")
    return r.json()

def first(d, *keys, default=None):
    for k in keys:
        if isinstance(d, dict) and d.get(k) not in (None, ""):
            return d[k]
    return default

def extract_total(rate):
    retail = rate.get("retailRate") or {}
    totals = retail.get("total") or []
    if totals and isinstance(totals[0], dict):
        try: return float(totals[0].get("amount")), totals[0].get("currency", "USD")
        except (TypeError, ValueError): pass
    for k in ("total", "amount"):
        try: return float(rate[k]), "USD"
        except (KeyError, TypeError, ValueError): pass
    return None, "USD"

def cancellation(rate):
    cp = rate.get("cancellationPolicies") or {}
    tag = str(cp.get("refundableTag") or "").upper()
    refundable = tag == "RFN"
    details = cp.get("cancelPolicyInfos") or cp.get("policies") or []
    if isinstance(details, dict): details = [details]
    return refundable, details

def room_class(room_name, rate):
    text = " ".join([str(room_name or ""), str(rate.get("roomName") or ""),
                     str(rate.get("name") or "")]).lower()
    if re.search(r"\b([2-9]|10)\s*[- ]?bedroom\b", text): return "2+ BR", 100
    if re.search(r"\b(one|1)\s*[- ]?bedroom\b", text): return "1 BR", 100
    if any(x in text for x in ("suite", "apartment", "villa", "residence")):
        return "Suite / residence — verify bedroom", 70
    if re.search(r"\b(two|2)\s+(double|queen|full)\b", text) or "double bed" in text or "twin bed" in text:
        return "Standard room — no separate bedroom evidence", 0
    if re.search(r"\bking\b|\bqueen\b|\bdouble\b|\btwin\b", text):
        return "Standard room — no separate bedroom evidence", 0
    return "Configuration unclear — verify bedroom", 30

def flatten_rates(raw):
    rows = []
    for hotel in raw.get("data", []) or []:
        hid = hotel.get("hotelId") or hotel.get("id")
        hotel_data = hotel.get("hotelData") or {}
        room_types = hotel.get("roomTypes") or []
        if not room_types and hotel.get("rates"): room_types = [{"rates": hotel.get("rates")}]
        for rt in room_types:
            for rate in rt.get("rates") or []:
                total, currency = extract_total(rate)
                if total is None: continue
                room_name = first(rate, "name", "roomName",
                                  default=first(rt, "name", "roomName", default="Room type not supplied"))
                refundable, details = cancellation(rate)
                label, bedroom_score = room_class(room_name, rate)
                rows.append({
                    "hotel_id": hid, "room": room_name, "room_label": label,
                    "bedroom_score": bedroom_score, "total": total, "currency": currency,
                    "refundable": refundable, "cancel_details": details,
                    "board": first(rate, "boardName", "board", default=""),
                    "rate_hotel_data": hotel_data
                })
    return rows

def parse_hotel_detail(raw):
    data = raw.get("data") or raw
    if isinstance(data, list): data = data[0] if data else {}
    if not isinstance(data, dict): return {}
    address = data.get("address") or {}
    if isinstance(address, str): address_text = address
    else:
        parts = [address.get("line1") or address.get("addressLine1"), address.get("city"),
                 address.get("state"), address.get("postalCode") or address.get("zipCode")]
        address_text = ", ".join(str(x) for x in parts if x)
    facilities = data.get("hotelFacilities") or data.get("facilities") or data.get("amenities") or []
    names = []
    for f in facilities:
        if isinstance(f, str): names.append(f)
        elif isinstance(f, dict):
            v = first(f, "name", "facilityName", "description")
            if v: names.append(str(v))
    rr = first(data, "rating")
    try: rr = float(rr)
    except (TypeError, ValueError): rr = None
    sr = first(data, "starRating", "stars")
    try: sr = float(sr)
    except (TypeError, ValueError): sr = None
    return {"name": first(data, "name", "hotelName", default=""),
            "address": address_text, "review_rating": rr, "star_rating": sr,
            "facilities": names}

def amenity_hits(facilities):
    text = " ".join(facilities).lower()
    return [x for key, x in [("laundry","Laundry"),("washer","Laundry"),
                             ("hot tub","Hot tub"),("jacuzzi","Hot tub"),
                             ("pool","Pool"),("ski","Ski"),("parking","Parking")]
            if key in text][:5]

def price_score(total, budget):
    if total <= budget: return 70 + 30*(budget-total)/budget
    if total <= budget*1.2: return 70 - 40*(total-budget)/(budget*.2)
    return max(0, 30 - 60*(total-budget*1.2)/budget)

def score_property(row, pref, budget):
    quality = min(100, max(0, (row.get("review_rating") or 7.5)*10))
    bw = .15 if pref in ("Required","Preferred") else 0
    return round(.30*price_score(row["total"], budget)+.20*quality+.15*75+
                 bw*row["bedroom_score"]+(.15-bw)*75+
                 .10*(95 if row["refundable"] else 50)+.05*60+.05*55,1)

# Booking comparison
def parse_money(text):
    # Prefer amounts explicitly marked with $ or USD, avoiding point counts.
    m = re.search(r'(?:USD\s*)?\$?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)\s*(?:USD)?', text, re.I)
    if not m: return None
    try: return float(m.group(1).replace(",",""))
    except ValueError: return None

def parse_points(text):
    for pat in [r'([0-9][0-9,]*)\s*(?:ultimate rewards|UR|chase points|points|pts)\b']:
        m = re.search(pat, text, re.I)
        if m:
            try: return int(m.group(1).replace(",",""))
            except ValueError: pass
    return None

def parse_booking(text):
    return {
        "cash": parse_money(text),
        "points": parse_points(text),
        "refundable": bool(re.search(r'\b(refundable|free cancellation|cancel free)\b', text, re.I)),
        "boost": (float(m.group(1)) if (m:=re.search(r'(?:points?\s*boost|boost)[^\d]{0,30}(1\.[0-9]+)\s*x', text, re.I)) else None)
    }


# -------- Bulk booking-source import --------
def normalize_name(name):
    s=(name or '').lower().replace('&','and')
    s=re.sub(r'[^a-z0-9]+',' ',s)
    return ' '.join(x for x in s.split() if x not in {'hotel','resort','lodge','inn','the'})

def _clean_line(x):
    return re.sub(r'\s+', ' ', x).strip().strip('*').strip()

def _is_generic_chase_line(x):
    low=x.lower().strip()
    return low in {
        'primary image','points boost','property amenity','property amenities',
        'ad','svg','svgsvg','image','star rating','original points',
        'new points boost','or','nightly average*','all-in total'
    } or low.startswith('photo gallery')

def parse_chase_bulk(text):
    lines=[_clean_line(x) for x in text.splitlines() if _clean_line(x)]
    out=[]
    # Find each "Star rating" marker. The property name is the closest
    # preceding non-generic line, which handles amenity/image labels.
    stars=[i for i,x in enumerate(lines) if x.lower()=='star rating']
    for n,si in enumerate(stars):
        next_si=stars[n+1] if n+1<len(stars) else len(lines)
        prev_candidates=[]
        for k in range(si-1,max(-1,si-8),-1):
            if not _is_generic_chase_line(lines[k]) and not lines[k].startswith('$'):
                prev_candidates.append(lines[k])
        if not prev_candidates: continue
        name=prev_candidates[0]
        chunk=lines[si:next_si]
        star=None; rating=None; total=None; pts=None; orig=None; due=None
        if len(chunk)>1:
            m=re.search(r'([0-9]+(?:\.[0-9]+)?)\s*star',chunk[1],re.I)
            if m: star=float(m.group(1))
        for j,x in enumerate(chunk):
            m=re.search(r'(?:Tripadvisor(?:s)? rating|Tripadvisor).*?([0-9]+(?:\.[0-9]+)?)',x,re.I)
            if m and rating is None: rating=float(m.group(1))
            if x.lower()=='all-in total' and j+1<len(chunk): total=parse_money(chunk[j+1])
            m=re.search(r'Original points\s*([0-9,]+)',x,re.I)
            if m: orig=int(m.group(1).replace(',',''))
            m=re.search(r'new points boost\s*([0-9,]+)',x,re.I)
            if m: pts=int(m.group(1).replace(',',''))
            m=re.search(r'\$([0-9,]+)\s*due at property',x,re.I)
            if m: due=float(m.group(1).replace(',',''))
        # Chase often repeats the point numbers on their own lines.
        if orig is None:
            for j,x in enumerate(chunk):
                if x.lower()=='original points' and j+1<len(chunk):
                    m=re.search(r'[0-9,]+',chunk[j+1]); orig=int(m.group(0).replace(',','')) if m else None
        if pts is None:
            for j,x in enumerate(chunk):
                if x.lower()=='new points boost' and j+1<len(chunk):
                    m=re.search(r'[0-9,]+',chunk[j+1]); pts=int(m.group(0).replace(',','')) if m else None
        sold=any(x.lower()=='sold out' for x in chunk)
        if total is not None or sold:
            out.append({'name':name,'source':'Chase Travel','cash':total,'points':pts,
                        'original_points':orig,'due':due,'rating':rating,'star':star,
                        'sold_out':sold,'boost':bool(pts and orig)})
    return out

def parse_expedia_bulk(text):
    lines=[_clean_line(x) for x in text.splitlines() if _clean_line(x)]
    out=[]
    # Prefer explicit "Photo gallery for ..." markers because they are an
    # excellent card boundary in Expedia's copied page text.
    starts=[]
    for i,x in enumerate(lines):
        m=re.search(r'photo gallery for\s+(.+?)(?:show previous|$)',x,re.I)
        if m: starts.append((i,m.group(1).strip('* ')))
    if starts:
        for n,(si,name) in enumerate(starts):
            ei=starts[n+1][0] if n+1<len(starts) else len(lines)
            chunk=lines[si:ei]
            joined=' '.join(chunk)
            total=None
            for j,x in enumerate(chunk):
                if re.search(r'current price is',x,re.I) and '$' in x:
                    total=parse_money(x); break
                if re.fullmatch(r'\$[0-9,]+ total',x,re.I):
                    total=parse_money(x); break
            if total is None:
                vals=[parse_money(x) for x in chunk if '$' in x and 'previous price' not in x.lower()]
                vals=[v for v in vals if v is not None]
                if vals: total=max(vals) if len(vals)>1 else vals[0]
            rating=None
            for x in chunk:
                m=re.search(r'(?:guest rating|tripadvisor.*?rating)\s*([0-9]+(?:\.[0-9]+)?)',x,re.I)
                if m: rating=float(m.group(1)); break
            refundable=bool(re.search(r'fully refundable|free cancellation|reserve now, pay later',joined,re.I))
            member=bool(re.search(r'member price|sign in for extra savings|vip access|one key silver',joined,re.I))
            discount=None
            m=re.search(r'member price\s*\$([0-9,]+)\s*off',joined,re.I)
            if m: discount=float(m.group(1).replace(',',''))
            # Pull Expedia hotel id from the property-information URL when present.
            hotel_id=None
            m=re.search(r'\.h(\d+)\.Hotel-Information',joined,re.I)
            if m: hotel_id=m.group(1)
            sold=bool(re.search(r'\bsold out\b',joined,re.I))
            if total is not None or sold:
                out.append({'name':name,'source':'Expedia / VRBO','cash':total,'rating':rating,
                            'refundable':refundable,'member':member,'discount':discount,
                            'hotel_id':hotel_id,'sold_out':sold})
        return out

    # Fallback for copied text without the photo-gallery markers: look for
    # explicit "X in new tab" / property-information URLs and nearby prices.
    for i,x in enumerate(lines):
        if not re.search(r'Hotel-Information',x,re.I): continue
        w=lines[max(0,i-20):i+2]; joined=' '.join(w)
        mname=None
        for y in reversed(w):
            if y.lower() not in {'stowe','more information about'} and not y.startswith('http') and '$' not in y:
                if len(y)>3: mname=y; break
        total=None
        mt=re.search(r'(?:current price is|total)\s*\$([0-9,]+)',joined,re.I)
        if mt: total=float(mt.group(1).replace(',',''))
        if total is not None and mname:
            out.append({'name':mname,'source':'Expedia / VRBO','cash':total,'rating':None,
                        'refundable':bool(re.search(r'fully refundable|free cancellation',joined,re.I)),
                        'member':bool(re.search(r'member price|one key silver|vip access',joined,re.I)),
                        'discount':None,'hotel_id':None,'sold_out':False})
    return out

def match_source_properties(props, rows):
    matches=[]
    for p in props:
        pn=normalize_name(p.get('name')); pt=set(pn.split())
        best=None; best_score=0.0
        for r in rows:
            rn=normalize_name(r.get('name')); rt=set(rn.split())
            if not rn: continue
            if pn==rn: sc=1.0
            elif pn in rn or rn in pn: sc=.92
            else:
                inter=len(pt&rt)
                sc=inter/max(1,len(pt|rt))
                # Strong boost when the distinctive tokens all agree.
                if len(pt&rt)>=2: sc=max(sc, .80)
            if sc>best_score:
                best_score=sc; best=r
        # Do not force a weak match. This prevents one wrong property from
        # being assigned another hotel's price.
        if best_score < .60: best=None
        matches.append((p,best,best_score))
    return matches


# -------- UI --------
st.title("✈️ Travel Optimizer")
st.caption("Phase 3.1 — live lodging search + bulk Chase/Expedia matching + booking strategy")

with st.sidebar:
    st.header("Trip")
    destination = st.text_input("Destination", "Stowe, VT")
    checkin = st.date_input("Check-in", date(2026,12,5))
    checkout = st.date_input("Check-out", date(2026,12,12))
    adults = st.number_input("Adults",1,10,2)
    child1 = st.number_input("Child 1 age",0,17,5)
    child2 = st.number_input("Child 2 age",0,17,7)
    budget = st.number_input("Target lodging budget (USD)",100,20000,3000,100)
    nationality = st.selectbox("Guest nationality",["US","CA"],0)
    hotel_limit = st.slider("Hotels to search",5,100,30)
    st.divider()
    bedroom_preference = st.selectbox("Separate bedroom",
        ["Required","Preferred","No preference"],0,
        help="Required excludes results without bedroom evidence. Preferred favors them but keeps other results. No preference ignores bedroom configuration.")
    st.divider()
    st.header("Live data connection")
    st.caption("Use the Nuitee SANDBOX key beginning with sand_.")
    api_key = st.text_input("Sandbox API key",type="password")
    run = st.button("🔎 Search & rank",type="primary",use_container_width=True)

if run:
    if checkout <= checkin: st.error("Check-out must be after check-in."); st.stop()
    if not api_key: st.error("Paste your Nuitee sandbox API key."); st.stop()
    payload = {"occupancies":[{"adults":int(adults),"children":[int(child1),int(child2)]}],
               "currency":"USD","guestNationality":nationality,
               "checkin":checkin.isoformat(),"checkout":checkout.isoformat(),
               "cityName":destination.split(",")[0].strip(),"countryCode":"US",
               "limit":int(hotel_limit),"maxRatesPerHotel":5,"roomMapping":True,
               "includeHotelData":True,"timeout":10,"sessionId":str(uuid4())}
    with st.spinner("Searching live rates and enriching properties..."):
        try:
            raw = api_post("/hotels/rates",payload,api_key)
            rates = flatten_rates(raw)
            grouped={}
            for r in rates:
                if r["hotel_id"]: grouped.setdefault(r["hotel_id"],[]).append(r)
            properties=[]; excluded=0
            for hid, rs in grouped.items():
                eligible=[r for r in rs if r["bedroom_score"]>0] if bedroom_preference=="Required" else rs
                if not eligible: excluded+=1; continue
                best=min(eligible,key=lambda x:x["total"]) if bedroom_preference=="No preference" else sorted(eligible,key=lambda x:(-x["bedroom_score"],x["total"]))[0]
                try: detail=parse_hotel_detail(api_get("/data/hotel",api_key,{"hotelId":hid}))
                except Exception: detail={}
                rd=best.get("rate_hotel_data") or {}
                best["name"]=detail.get("name") or rd.get("name") or f"Hotel {hid}"
                best["address"]=detail.get("address") or ""
                best["review_rating"]=detail.get("review_rating")
                best["star_rating"]=detail.get("star_rating")
                best["facilities"]=detail.get("facilities",[])
                best["amenity_hits"]=amenity_hits(best["facilities"])
                best["score"]=score_property(best,bedroom_preference,budget)
                properties.append(best)
            properties.sort(key=lambda x:x["score"],reverse=True)
            st.session_state.update(properties=properties,excluded=excluded,raw_count=len(rates),search_error=None,comparison=None)
        except Exception as exc:
            st.session_state.update(properties=[],search_error=str(exc),comparison=None)

if st.session_state.get("search_error"): st.error(f"Search failed: {st.session_state['search_error']}")
properties=st.session_state.get("properties",[])

if properties:
    st.success(f"Found {len(properties)} candidate properties from {st.session_state.get('raw_count',0)} live room-rate options.")
    if bedroom_preference=="Required" and st.session_state.get("excluded",0):
        st.info(f"Filtered out {st.session_state['excluded']} properties because none of their returned rooms showed evidence of a private bedroom.")
    elif bedroom_preference=="Preferred": st.info("Bedroom configuration is a ranking preference, not a filter.")
    else: st.info("Bedroom configuration is not being used as a filter or ranking factor.")

    st.subheader("🏆 Ranked shortlist")
    for i,p in enumerate(properties[:10],1):
        with st.container(border=True):
            c1,c2,c3=st.columns([5,2,2])
            with c1:
                st.subheader(f"{i}. {p['name']}")
                if p["address"]: st.caption(p["address"])
                st.write(f"**Room:** {p['room']}  ·  {'🛏️' if p['bedroom_score']==100 else '⚠️'} **{p['room_label']}**")
                if p["amenity_hits"]: st.caption("Amenities: "+" · ".join(p["amenity_hits"]))
            with c2:
                st.metric("Stay total",f"${p['total']:,.0f}")
                st.write(f"Optimizer score: **{p['score']}/100**")
                if p.get("review_rating") is not None: st.write(f"Guest rating: **{p['review_rating']:.1f}/10**")
                if p.get("star_rating") is not None: st.write(f"Hotel class: **{p['star_rating']:g}/5 stars**")
            with c3:
                st.write("**Cancellation:** "+("Refundable" if p["refundable"] else "Not detected"))
                st.caption(f"Hotel ID: {p['hotel_id']}")

    st.divider()
    st.subheader("📥 Bulk Booking-Source Import")
    st.caption("Run the same search on Chase Travel and Expedia while logged in, then paste the complete copied results. The app matches them to the Nuitee shortlist.")
    a,b=st.columns(2)
    with a: chase_bulk=st.text_area("Chase Travel — full search results",height=240,key="chase_bulk",placeholder="Paste everything exactly as copied.")
    with b: expedia_bulk=st.text_area("Expedia — full search results",height=240,key="expedia_bulk",placeholder="Paste everything exactly as copied.")
    if st.button("🔗 Match & Analyze All Sources",type="primary"):
        st.session_state['bulk_matches']={'chase':match_source_properties(properties,parse_chase_bulk(chase_bulk) if chase_bulk.strip() else []),'expedia':match_source_properties(properties,parse_expedia_bulk(expedia_bulk) if expedia_bulk.strip() else [])}
    bulk=st.session_state.get('bulk_matches')
    if bulk:
        rows=[]
        for p in properties[:10]:
            c=next((x[1] for x in bulk['chase'] if x[0] is p),None); e=next((x[1] for x in bulk['expedia'] if x[0] is p),None)
            rows.append({'Property':p['name'],'Nuitee low':f"${p['total']:,.0f}",'Chase low':(f"${c['cash']:,.0f}" if c and c.get('cash') is not None else ('Sold out' if c and c.get('sold_out') else '—')),'Chase Boost pts':(f"{c['points']:,}" if c and c.get('points') else '—'),'Chase value':(f"{c['cash']/c['points']*100:.2f}¢/pt" if c and c.get('cash') and c.get('points') else '—'),'Expedia low':(f"${e['cash']:,.0f}" if e and e.get('cash') is not None else '—'),'Expedia perks':('Member/VIP' if e and e.get('member') else '—'),'Nuitee room':p['room']})
        st.dataframe(rows,use_container_width=True,hide_index=True)
        st.caption('Nuitee is the automated low-rate baseline. Chase and Expedia values are imported from the pasted search summaries; room types remain unverified until a finalist is checked.')

    st.divider()
    st.subheader("💳 Booking Strategy")
    st.caption("Add results from sources we cannot reliably query directly. Chase points have a hard 1¢/point cash-out floor.")

    selected_name=st.selectbox("Choose a finalist",[p["name"] for p in properties[:10]],key="booking_property")
    selected=next(p for p in properties if p["name"]==selected_name)
    st.write(f"**{selected['name']}** — Nuitee: **${selected['total']:,.0f}**")

    sources=[
        ("Chase Travel","Paste the exact Chase result. Include cash price, points price, and any Points Boost text."),
        ("Hyatt","Paste the Hyatt cash and/or award result."),
        ("Marriott","Paste the Marriott cash and/or award result."),
        ("IHG","Paste the IHG cash and/or award result."),
        ("Wyndham","Paste the Wyndham cash and/or award result."),
        ("Expedia / VRBO","Paste the Expedia or VRBO result, including member pricing if shown."),
        ("Direct hotel","Paste the direct-booking result.")
    ]
    cols=st.columns(2)
    for idx,(source,help_text) in enumerate(sources):
        with cols[idx%2]:
            with st.expander(source,expanded=(source=="Chase Travel")):
                st.text_area("Paste result",key=f"paste_{source}",height=105,
                             placeholder="Example: $1,985 or 132,333 points. Free cancellation...",help=help_text)
                txt=st.session_state.get(f"paste_{source}","")
                if txt:
                    q=parse_booking(txt)
                    st.caption(f"Detected cash: {('$'+format(q['cash'],',.2f')) if q['cash'] is not None else '—'} · Points: {(format(q['points'],',')+' pts') if q['points'] else '—'} · Refundable: {'Yes' if q['refundable'] else 'Not detected'} · Boost: {(str(q['boost'])+'x') if q['boost'] else '—'}")

    if st.button("🧮 Compare booking options",type="primary"):
        opts=[{"source":"Nuitee","cash":selected["total"],"points":None,"refundable":selected["refundable"]}]
        for source,_ in sources:
            txt=st.session_state.get(f"paste_{source}","")
            if txt.strip():
                q=parse_booking(txt)
                opts.append({"source":source,"cash":q["cash"],"points":q["points"],"refundable":q["refundable"]})
        st.session_state["comparison"]=opts

    comparison=st.session_state.get("comparison")
    if comparison:
        st.markdown("#### Comparison")
        display=[]
        for x in comparison:
            cpp=(x["cash"]/x["points"]) if x.get("cash") is not None and x.get("points") else None
            display.append({"Booking source":x["source"],
                            "Cash":f"${x['cash']:,.0f}" if x.get("cash") is not None else "—",
                            "Points":f"{x['points']:,}" if x.get("points") else "—",
                            "Redemption":f"{cpp*100:.2f}¢/pt" if cpp is not None else "—",
                            "Cancellation":"Refundable" if x.get("refundable") else "Not detected"})
        st.dataframe(display,use_container_width=True,hide_index=True)

        point_opts=[]
        for x in comparison:
            if x.get("cash") is not None and x.get("points"):
                x["cpp"]=x["cash"]/x["points"]; point_opts.append(x)
        if point_opts:
            best=max(point_opts,key=lambda x:x["cpp"])
            if best["cpp"]<.01:
                st.error(f"{best['source']} is below your 1¢/point floor. Paying cash is mathematically better.")
            elif best["cpp"]<.015:
                st.info(f"{best['source']} gives {best['cpp']*100:.2f}¢/point. Above your floor, but not automatically better than cash.")
            else:
                st.success(f"{best['source']} gives {best['cpp']*100:.2f}¢/point — a strong redemption worth considering.")
        st.caption("Pasted results are user-supplied and are not independently verified by the app.")
else:
    st.info("Enter the trip and Nuitee sandbox key, then choose **Search & rank**.")
