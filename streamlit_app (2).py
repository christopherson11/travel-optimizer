
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
    text = " ".join([
        str(room_name or ""), str(rate.get("roomName") or ""),
        str(rate.get("name") or "")
    ]).lower()

    # Explicit bedroom counts first.
    m = re.search(r"\b([1-9]|10)\s*[- ]?bedroom\b", text)
    if m:
        n = int(m.group(1))
        return f"{n} BR", n

    word_counts = {"one": 1, "two": 2, "three": 3, "four": 4}
    for word, n in word_counts.items():
        if re.search(rf"\b{word}\s*[- ]?bedroom\b", text):
            return f"{n} BR", n

    # Suites/residences are useful candidates, but their bedroom count is not
    # guaranteed by the word "suite" alone.
    if any(x in text for x in ("suite", "apartment", "villa", "residence", "condo")):
        return "Unknown BR — verify", 0

    return "Unknown BR", 0

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
    bedroom_bonus = 0
    if pref != "Any":
        min_br = int(pref.split("+")[0])
        if row.get("bedroom_score", 0) >= min_br:
            bedroom_bonus = 100
        elif row.get("bedroom_score", 0) == 0:
            bedroom_bonus = 45

    return round(
        .30*price_score(row["total"], budget)
        + .20*quality
        + .15*75
        + .15*bedroom_bonus
        + .10*(95 if row["refundable"] else 50)
        + .05*60
        + .05*55, 1
    )


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
def _clean_line(x):
    return re.sub(r"\s+", " ", str(x)).strip()

def _is_generic_chase_line(x):
    return x.lower() in {"primary image", "pool", "points boost", "property amenity", "indoor pool", "exterior", "room", "fitness facility", "terrace/patio", "star rating"}

def normalize_name(name):
    s = (name or "").lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    # These words are too generic to identify a hotel. Removing them prevents
    # false matches such as "Tälta Lodge" -> "The Lodge at Spruce Peak".
    stop = {
        "the", "hotel", "hotels", "resort", "resorts", "lodge", "lodges", "inn",
        "and", "a", "an", "at", "by", "of", "on", "in", "the", "family",
        "destination", "residence", "residences"
    }
    return " ".join(x for x in s.split() if x not in stop)

def parse_chase_bulk(text):
    # Chase cards are reliably delimited by "Points Boost". The property name
    # is the line immediately following that marker.
    lines = [_clean_line(x) for x in text.splitlines() if _clean_line(x)]
    out = []
    positions = [i for i, x in enumerate(lines) if x.lower() == "points boost"]

    for n, pos in enumerate(positions):
        end = positions[n + 1] if n + 1 < len(positions) else len(lines)
        block = lines[pos:end]
        if pos + 1 >= len(lines):
            continue

        name = lines[pos + 1]
        if _is_generic_chase_line(name) or name.lower() in {"star rating", "sold out"}:
            continue

        total = orig = pts = due = star = rating = None
        for j, x in enumerate(block):
            if x.lower() == "all-in total" and j + 1 < len(block):
                total = parse_money(block[j + 1])

            m = re.search(r'Original points\s*([0-9,]+)', x, re.I)
            if m:
                orig = int(m.group(1).replace(",", ""))

            m = re.search(r'new points boost\s*([0-9,]+)', x, re.I)
            if m:
                pts = int(m.group(1).replace(",", ""))

            m = re.search(r'\$([0-9,]+(?:\.[0-9]+)?)\s*due at property', x, re.I)
            if m:
                due = float(m.group(1).replace(",", ""))

            m = re.fullmatch(r'([0-9]+(?:\.[0-9]+)?)\s*star', x, re.I)
            if m:
                star = float(m.group(1))

            m = re.search(r'(?:Tripadvisors? rating|Tripadvisor).*?([0-9]+(?:\.[0-9]+)?)', x, re.I)
            if m and rating is None:
                rating = float(m.group(1))

        # Chase repeats the point counts as standalone lines after "or".
        if orig is None:
            for j, x in enumerate(block):
                if x.lower() == "or" and j + 1 < len(block):
                    m = re.fullmatch(r'([0-9,]+)', block[j + 1])
                    if m:
                        orig = int(m.group(1).replace(",", ""))
                        break

        if pts is None and orig is not None:
            # Find the first different standalone integer after the original count.
            for j, x in enumerate(block):
                if x.replace(",", "") == str(orig):
                    for y in block[j + 1:j + 5]:
                        m = re.fullmatch(r'([0-9,]+)', y)
                        if m:
                            candidate = int(m.group(1).replace(",", ""))
                            if candidate != orig:
                                pts = candidate
                                break
                    if pts is not None:
                        break

        sold = any(x.lower() == "sold out" for x in block)
        if total is not None or sold:
            out.append({
                "name": name, "source": "Chase Travel", "cash": total,
                "points": pts, "original_points": orig, "due": due,
                "rating": rating, "star": star, "sold_out": sold,
                "boost": bool(pts and orig)
            })
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

def match_score(a, b):
    aa = normalize_name(a)
    bb = normalize_name(b)
    if not aa or not bb:
        return 0.0
    if aa == bb:
        return 1.0

    at, bt = set(aa.split()), set(bb.split())
    inter = at & bt
    if not inter:
        return 0.0

    # Exact/near-exact distinctive-token containment is strong. Otherwise use
    # Jaccard overlap. Never promote a match merely because two generic words
    # (e.g. "a", "by", "lodge") happen to overlap.
    jaccard = len(inter) / max(1, len(at | bt))
    containment = len(inter) / max(1, min(len(at), len(bt)))

    if containment >= 1.0:
        return 0.92
    if jaccard >= 0.50:
        return min(0.90, jaccard + 0.20)
    return jaccard

def match_source_properties(props, rows):
    matches = []
    for p in props:
        best = None
        best_score = 0.0
        for r in rows:
            sc = match_score(p.get("name"), r.get("name"))
            if sc > best_score:
                best_score = sc
                best = r

        # Conservative threshold: an unmatched hotel is preferable to a
        # phantom price/points match.
        if best_score < 0.78:
            best = None
            best_score = 0.0
        matches.append((p, best, best_score))
    return matches


def merge_external_candidates(nuitee_props, chase_rows, expedia_rows):
    """Build a master property pool. Nuitee is one contributor, not the gatekeeper."""
    master = []

    # Start with Nuitee candidates, preserving all of their rich room data.
    for p in nuitee_props:
        item = dict(p)
        item["sources"] = {"Nuitee": p}
        item["chase_match"] = None
        item["expedia_match"] = None
        master.append(item)

    def attach_or_add(rows, source_key):
        for r in rows:
            best_idx = None
            best = 0.0
            for idx, m in enumerate(master):
                sc = match_score(m.get("name",""), r.get("name",""))
                if sc > best:
                    best = sc; best_idx = idx
            if best_idx is not None and best >= 0.78:
                master[best_idx]["sources"][source_key] = r
                master[best_idx][f"{source_key.lower()}_match"] = r
            else:
                # A property found only by Chase/Expedia must still enter the pool.
                master.append({
                    "name": r.get("name") or "Unknown property",
                    "room": "Not supplied by source",
                    "room_label": "Unknown BR",
                    "bedroom_score": 0,
                    "total": r.get("cash") if r.get("cash") is not None else 0,
                    "currency": "USD",
                    "refundable": bool(r.get("refundable")),
                    "cancel_details": [],
                    "board": "",
                    "hotel_id": None,
                    "address": "",
                    "review_rating": r.get("rating"),
                    "star_rating": r.get("star"),
                    "facilities": [],
                    "amenity_hits": [],
                    "score": None,
                    "sources": {source_key: r},
                    "chase_match": r if source_key == "Chase" else None,
                    "expedia_match": r if source_key == "Expedia" else None,
                    "external_only": True
                })

    attach_or_add(chase_rows, "Chase")
    attach_or_add(expedia_rows, "Expedia")
    return master

def source_cash(row, key):
    r = row.get("sources", {}).get(key)
    return r.get("cash") if r else None

def source_points(row):
    r = row.get("sources", {}).get("Chase")
    return r.get("points") if r else None

def source_due(row):
    r = row.get("sources", {}).get("Chase")
    return r.get("due") if r else None

def source_match_label(row):
    keys = list(row.get("sources", {}).keys())
    return ", ".join(keys) if keys else "—"


# -------- UI --------
# -------- UI --------
st.title("✈️ Travel Optimizer")
st.caption("Phase 4 — master property pool + bulk Chase/Expedia matching + standard bedroom filters")

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
    bedroom_preference = st.selectbox(
        "Bedrooms",
        ["Any", "1+", "2+", "3+", "4+"], 1,
        help="Uses explicit bedroom-count evidence when available. Unknown room configurations remain unverified rather than being treated as a specific bedroom count."
    )
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
                if bedroom_preference == "Any":
                    eligible = rs
                else:
                    min_br = int(bedroom_preference.split("+")[0])
                    eligible = [r for r in rs if r["bedroom_score"] >= min_br]
                if not eligible:
                    excluded += 1
                    continue
                best = min(eligible, key=lambda x:x["total"])
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
    if bedroom_preference != "Any" and st.session_state.get("excluded",0):
        st.info(f"Filtered out {st.session_state['excluded']} properties because the returned rooms did not show the requested bedroom count.")
    else:
        st.info(f"Bedroom filter: **{bedroom_preference}**. Room configurations without explicit bedroom evidence are shown as unverified.")

    st.subheader("🏆 Ranked shortlist")
    for i,p in enumerate(properties[:10],1):
        with st.container(border=True):
            c1,c2,c3=st.columns([5,2,2])
            with c1:
                st.subheader(f"{i}. {p['name']}")
                if p["address"]: st.caption(p["address"])
                st.write(f"**Room:** {p['room']}  ·  {'🛏️' if p['bedroom_score']>0 else '⚠️'} **{p['room_label']}**")
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
    st.caption(
        "Paste the complete Chase and/or Expedia search results. These sources can add "
        "properties that Nuitee did not return; Nuitee is no longer the gatekeeper."
    )
    a,b=st.columns(2)
    with a:
        chase_bulk=st.text_area(
            "Chase Travel — full search results", height=240, key="chase_bulk",
            placeholder="Paste everything exactly as copied."
        )
    with b:
        expedia_bulk=st.text_area(
            "Expedia — full search results", height=240, key="expedia_bulk",
            placeholder="Paste everything exactly as copied."
        )

    if st.button("🔗 Build Master Property Pool",type="primary"):
        chase_rows = parse_chase_bulk(chase_bulk) if chase_bulk.strip() else []
        exp_rows = parse_expedia_bulk(expedia_bulk) if expedia_bulk.strip() else []
        master = merge_external_candidates(properties, chase_rows, exp_rows)
        st.session_state["master_pool"] = master
        st.session_state["bulk_counts"] = {
            "chase": len(chase_rows), "expedia": len(exp_rows)
        }

    master = st.session_state.get("master_pool")
    if master:
        counts = st.session_state.get("bulk_counts", {})
        st.success(
            f"Master pool: **{len(master)} properties** "
            f"({len(properties)} from Nuitee + {counts.get('chase',0)} Chase results + "
            f"{counts.get('expedia',0)} Expedia results, after matching/deduplication)."
        )

        display=[]
        for p in master:
            c=p.get("sources",{}).get("Chase")
            e=p.get("sources",{}).get("Expedia")
            n=p.get("sources",{}).get("Nuitee")
            chase_cash = c.get("cash") if c else None
            exp_cash = e.get("cash") if e else None
            n_cash = n.get("total") if n else None
            chase_pts = c.get("points") if c else None
            due = c.get("due") if c else None
            cpp = ((chase_cash-(due or 0))/chase_pts*100) if chase_cash and chase_pts else None

            display.append({
                "Property": p["name"],
                "Sources": source_match_label(p),
                "Nuitee low": f"${n_cash:,.0f}" if n_cash is not None else "—",
                "Chase low": f"${chase_cash:,.0f}" if chase_cash is not None else ("Sold out" if c and c.get("sold_out") else "—"),
                "Chase Boost pts": f"{chase_pts:,}" if chase_pts else "—",
                "Chase value": f"{cpp:.2f}¢/pt" if cpp is not None else "—",
                "Expedia low": f"${exp_cash:,.0f}" if exp_cash is not None else "—",
                "Room evidence": p.get("room_label","Unknown BR")
            })
        st.dataframe(display,use_container_width=True,hide_index=True)

        st.caption(
            "A property can enter the master pool from any source. "
            "Prices are source-specific lowest rates unless a room-level result is later verified."
        )

        st.markdown("#### 🔎 Best apparent opportunities")
        # Simple source-agnostic cash view: lowest known cash price among all sources.
        candidates=[]
        for p in master:
            prices=[]
            n=p.get("sources",{}).get("Nuitee")
            c=p.get("sources",{}).get("Chase")
            e=p.get("sources",{}).get("Expedia")
            if n and n.get("total") is not None: prices.append(("Nuitee",n["total"]))
            if c and c.get("cash") is not None: prices.append(("Chase",c["cash"]))
            if e and e.get("cash") is not None: prices.append(("Expedia",e["cash"]))
            if prices:
                best_source,best_cash=min(prices,key=lambda x:x[1])
                candidates.append((best_cash,p,best_source))
        for best_cash,p,best_source in sorted(candidates,key=lambda x:x[0])[:8]:
            st.write(
                f"**{p['name']}** — lowest imported cash: **${best_cash:,.0f} via {best_source}** "
                f"· sources: {source_match_label(p)} · room: {p.get('room_label','Unknown BR')}"
            )

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
