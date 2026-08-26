
import re
import requests
import streamlit as st
from datetime import date
from uuid import uuid4

st.set_page_config(page_title="Travel Optimizer", page_icon="✈️", layout="wide")

API_BASE = "https://api.liteapi.travel/v3.0"

# ---------------- API ----------------
def api_get(path, key, params=None):
    r = requests.get(
        API_BASE + path,
        params=params or {},
        headers={"X-API-Key": key, "Accept": "application/json"},
        timeout=20,
    )
    if r.status_code == 401:
        raise RuntimeError("Nuitee rejected the API key (401). Use the sandbox key that starts with sand_.")
    if not r.ok:
        try:
            detail = r.json()
        except Exception:
            detail = r.text
        raise RuntimeError(f"Nuitee returned HTTP {r.status_code}: {detail}")
    return r.json()

def api_post(path, payload, key):
    r = requests.post(
        API_BASE + path,
        json=payload,
        headers={
            "X-API-Key": key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        timeout=35,
    )
    if r.status_code == 401:
        raise RuntimeError("Nuitee rejected the API key (401). Use the sandbox key that starts with sand_.")
    if not r.ok:
        try:
            detail = r.json()
        except Exception:
            detail = r.text
        raise RuntimeError(f"Nuitee returned HTTP {r.status_code}: {detail}")
    return r.json()

# ---------------- parsing helpers ----------------
def first(d, *keys, default=None):
    for k in keys:
        if isinstance(d, dict) and d.get(k) not in (None, ""):
            return d[k]
    return default

def extract_total(rate):
    retail = rate.get("retailRate") or {}
    totals = retail.get("total") or []
    if totals and isinstance(totals[0], dict):
        try:
            return float(totals[0].get("amount")), totals[0].get("currency", "USD")
        except (TypeError, ValueError):
            pass
    for k in ("total", "amount"):
        try:
            return float(rate[k]), "USD"
        except (KeyError, TypeError, ValueError):
            pass
    return None, "USD"

def cancellation(rate):
    cp = rate.get("cancellationPolicies") or {}
    tag = str(cp.get("refundableTag") or "").upper()
    refundable = tag == "RFN"
    details = cp.get("cancelPolicyInfos") or cp.get("policies") or []
    if isinstance(details, dict):
        details = [details]
    return refundable, details

def room_class(room_name, rate):
    text = " ".join([
        str(room_name or ""),
        str(rate.get("roomName") or ""),
        str(rate.get("name") or ""),
    ]).lower()

    # Strong evidence for a true private-bedroom configuration.
    if re.search(r"\b([2-9]|10)\s*[- ]?bedroom\b", text):
        return "2+ BR", 100
    if re.search(r"\b(one|1)\s*[- ]?bedroom\b", text):
        return "1 BR", 100

    # Common suite language: useful but not proof of a private bedroom.
    if "suite" in text or "apartment" in text or "villa" in text or "residence" in text:
        return "Suite / residence — verify bedroom", 70

    # Clearly not what the user wants.
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

        if not room_types and hotel.get("rates"):
            room_types = [{"rates": hotel.get("rates")}]

        for rt in room_types:
            for rate in rt.get("rates") or []:
                total, currency = extract_total(rate)
                if total is None:
                    continue

                room_name = first(
                    rate, "name", "roomName",
                    default=first(rt, "name", "roomName", default="Room type not supplied")
                )
                refundable, cancel_details = cancellation(rate)
                room_label, bedroom_score = room_class(room_name, rate)

                rows.append({
                    "hotel_id": hid,
                    "room": room_name,
                    "room_label": room_label,
                    "bedroom_score": bedroom_score,
                    "total": total,
                    "currency": currency,
                    "refundable": refundable,
                    "cancel_details": cancel_details,
                    "board": first(rate, "boardName", "board", default=""),
                    "mapped_room_id": first(rate, "mappedRoomId", "mappedRoomID"),
                    "raw_rate": rate,
                })
    return rows

def parse_hotel_detail(raw):
    data = raw.get("data") or raw
    if isinstance(data, list):
        data = data[0] if data else {}
    if not isinstance(data, dict):
        return {}

    # Nuitee's content API can vary slightly by response shape.
    name = first(data, "name", "hotelName", default="")
    address = data.get("address") or {}
    if isinstance(address, str):
        address_text = address
    else:
        parts = [
            address.get("line1") or address.get("addressLine1"),
            address.get("city"),
            address.get("state"),
            address.get("postalCode") or address.get("zipCode"),
        ]
        address_text = ", ".join(str(x) for x in parts if x)

    facilities = (
        data.get("hotelFacilities")
        or data.get("facilities")
        or data.get("amenities")
        or []
    )
    facility_names = []
    for f in facilities:
        if isinstance(f, str):
            facility_names.append(f)
        elif isinstance(f, dict):
            v = first(f, "name", "facilityName", "description")
            if v:
                facility_names.append(str(v))

    images = data.get("images") or data.get("photos") or []
    image_url = None
    if images:
        img = images[0]
        image_url = img if isinstance(img, str) else first(img, "url", "imageUrl", "href")

    review_rating = first(data, "rating")
    try:
        review_rating = float(review_rating)
    except (TypeError, ValueError):
        review_rating = None

    star_rating = first(data, "starRating", "stars")
    try:
        star_rating = float(star_rating)
    except (TypeError, ValueError):
        star_rating = None

    return {
        "name": name,
        "address": address_text,
        "review_rating": review_rating,
        "star_rating": star_rating,
        "facilities": facility_names,
        "image": image_url,
        "description": first(data, "description", "shortDescription", default=""),
        "lat": first(data, "latitude", "lat"),
        "lon": first(data, "longitude", "lng", "lon"),
        "raw": data,
    }

# ---------------- scoring ----------------
def price_score(total, budget):
    if total <= budget:
        return 70 + 30 * (budget-total) / budget
    if total <= budget * 1.20:
        return 70 - 40 * (total-budget) / (budget*.20)
    return max(0, 30 - 60 * (total-budget*1.20) / budget)

def score_property(row, budget):
    price = price_score(row["total"], budget)
    review_rating = row.get("review_rating")
    if review_rating is None:
        quality = 75
    else:
        # Nuitee review ratings are on a 0-10 scale.
        quality = min(100, max(0, float(review_rating) * 10))

    # Location/amenities/loyalty are intentionally conservative until
    # we add destination-aware and loyalty-specific sources.
    location = 75
    amenities = 60
    loyalty = 55
    cancellation = 95 if row["refundable"] else 50
    bedroom = row["bedroom_score"]

    # Bedroom weighting is only active when it is a user preference.
    # The caller sets row["bedroom_weight"] based on the selected option.
    bedroom_weight = row.get("bedroom_weight", 0.15)
    other_weight = .15 - bedroom_weight

    return round(
        .30*price + .20*quality + .15*location + bedroom_weight*bedroom
        + other_weight*75 + .10*cancellation + .05*amenities + .05*loyalty, 1
    )

def amenity_hits(facilities):
    text = " ".join(facilities).lower()
    hits = []
    if "laundry" in text or "washer" in text:
        hits.append("Laundry")
    if "hot tub" in text or "jacuzzi" in text:
        hits.append("Hot tub")
    if "pool" in text:
        hits.append("Pool")
    if "ski" in text:
        hits.append("Ski")
    if "parking" in text:
        hits.append("Parking")
    return hits

# ---------------- UI ----------------
st.title("✈️ Travel Optimizer")
st.caption("Phase 2.7 — live rates + property enrichment + configurable preferences")

with st.sidebar:
    st.header("Trip")
    destination = st.text_input("Destination", "Stowe, VT")
    checkin = st.date_input("Check-in", date(2026, 12, 5))
    checkout = st.date_input("Check-out", date(2026, 12, 12))
    adults = st.number_input("Adults", 1, 10, 2)
    child1 = st.number_input("Child 1 age", 0, 17, 5)
    child2 = st.number_input("Child 2 age", 0, 17, 7)
    budget = st.number_input("Target lodging budget (USD)", 100, 20000, 3000, 100)
    nationality = st.selectbox("Guest nationality", ["US", "CA"], index=0)
    hotels_to_search = st.slider("Hotels to search", 5, 100, 30)

    st.divider()
    st.header("Room configuration")
    bedroom_preference = st.selectbox(
        "Separate bedroom",
        ["Required", "Preferred", "No preference"],
        index=0,
        help=(
            "Required = exclude results without evidence of a private bedroom. "
            "Preferred = keep everything, but favor properties with bedroom evidence. "
            "No preference = do not use bedroom configuration in the ranking."
        ),
    )
    st.caption(
        "For this Stowe test, leave it on **Required**. "
        "For future trips, you can change it without rebuilding the search."
    )

    st.divider()
    st.header("Live data connection")
    st.caption("Use the Nuitee SANDBOX key beginning with `sand_`.")
    api_key = st.text_input("Sandbox API key", type="password")

    run = st.button("🔎 Search & rank", type="primary", use_container_width=True)

if run:
    if checkout <= checkin:
        st.error("Check-out must be after check-in.")
        st.stop()
    if not api_key:
        st.error("Paste your Nuitee sandbox API key.")
        st.stop()

    city = destination.split(",")[0].strip()
    payload = {
        "occupancies": [{"adults": int(adults), "children": [int(child1), int(child2)]}],
        "currency": "USD",
        "guestNationality": nationality,
        "checkin": checkin.isoformat(),
        "checkout": checkout.isoformat(),
        "cityName": city,
        "countryCode": "US",
        "limit": int(hotels_to_search),
        "maxRatesPerHotel": 5,
        "roomMapping": True,
        "includeHotelData": True,
        "timeout": 10,
        "sessionId": str(uuid4()),
    }

    with st.spinner("Searching live rates and enriching properties..."):
        try:
            raw = api_post("/hotels/rates", payload, api_key)
            rate_rows = flatten_rates(raw)

            # Group rates by hotel. Pick the best family-compatible rate per property.
            grouped = {}
            for r in rate_rows:
                hid = r["hotel_id"]
                if not hid:
                    continue
                grouped.setdefault(hid, []).append(r)

            properties = []
            excluded = 0

            for hid, rates in grouped.items():
                # Required: only return properties with some bedroom evidence.
                # Preferred: keep all properties, but choose/rank the strongest
                # configuration first.
                # No preference: choose the cheapest rate at each property.
                if bedroom_preference == "Required":
                    eligible = [r for r in rates if r["bedroom_score"] > 0]
                    if not eligible:
                        excluded += 1
                        continue
                else:
                    eligible = rates

                if bedroom_preference == "No preference":
                    best = min(eligible, key=lambda x: x["total"])
                else:
                    best = sorted(
                        eligible,
                        key=lambda x: (-x["bedroom_score"], x["total"])
                    )[0]

                # Current rates responses can already contain brief hotel data.
                rate_hotel = {}
                for candidate in (
                    raw.get("hotels", []) if isinstance(raw, dict) else [],
                    [x.get("hotelData", {}) for x in (raw.get("data", []) if isinstance(raw, dict) else [])],
                ):
                    if not isinstance(candidate, list):
                        continue
                    for item in candidate:
                        if not isinstance(item, dict):
                            continue
                        candidate_id = item.get("id") or item.get("hotelId")
                        if candidate_id == hid:
                            rate_hotel = item
                            break
                    if rate_hotel:
                        break

                try:
                    detail_raw = api_get(
                        "/data/hotel",
                        api_key,
                        params={"hotelId": hid, "timeout": 1.5},
                    )
                    detail = parse_hotel_detail(detail_raw)
                except Exception:
                    detail = {}

                best["hotel_id"] = hid
                best["name"] = (
                    detail.get("name")
                    or rate_hotel.get("name")
                    or best.get("name")
                    or f"Hotel {hid}"
                )
                best["address"] = (
                    detail.get("address")
                    or rate_hotel.get("address")
                    or best.get("address", "")
                )
                best["review_rating"] = (
                    detail.get("review_rating")
                    if detail.get("review_rating") is not None
                    else rate_hotel.get("rating")
                )
                best["star_rating"] = (
                    detail.get("star_rating")
                    if detail.get("star_rating") is not None
                    else rate_hotel.get("starRating")
                )
                best["facilities"] = detail.get("facilities", [])
                best["image"] = detail.get("image") or rate_hotel.get("main_photo")
                best["description"] = detail.get("description", "")
                best["amenity_hits"] = amenity_hits(best["facilities"])
                best["bedroom_weight"] = (
                    0.15 if bedroom_preference in ("Required", "Preferred") else 0.0
                )
                best["score"] = score_property(best, budget)
                properties.append(best)

            properties.sort(key=lambda x: x["score"], reverse=True)
            st.session_state["properties"] = properties
            st.session_state["excluded"] = excluded
            st.session_state["raw_count"] = len(rate_rows)
            st.session_state["search_error"] = None

        except Exception as exc:
            st.session_state["properties"] = []
            st.session_state["search_error"] = str(exc)

error = st.session_state.get("search_error")
if error:
    st.error(f"Search failed: {error}")

properties = st.session_state.get("properties", [])
if properties:
    excluded = st.session_state.get("excluded", 0)
    raw_count = st.session_state.get("raw_count", 0)

    st.success(
        f"Found {len(properties)} candidate properties from {raw_count} live room-rate options."
    )

    if bedroom_preference == "Required" and excluded:
        st.info(
            f"Filtered out {excluded} properties because none of their returned rooms "
            "showed evidence of a private bedroom."
        )
    elif bedroom_preference == "Preferred":
        st.info(
            "Bedroom configuration is being used as a ranking preference, "
            "but properties without bedroom evidence are still included."
        )
    elif bedroom_preference == "No preference":
        st.info(
            "Bedroom configuration is not being used as a filter or ranking factor."
        )

    st.subheader("🏆 Ranked shortlist")

    for i, p in enumerate(properties[:10], 1):
        with st.container(border=True):
            c1, c2, c3 = st.columns([5, 2, 2])

            with c1:
                st.subheader(f"{i}. {p['name']}")
                if p["address"]:
                    st.caption(p["address"])

                room_label = p["room_label"]
                if p["bedroom_score"] == 100:
                    st.write(f"**Room:** {p['room']}  ·  🛏️ **{room_label}**")
                else:
                    st.write(f"**Room:** {p['room']}  ·  ⚠️ **{room_label}**")

                if p["amenity_hits"]:
                    st.caption("Amenities: " + " · ".join(p["amenity_hits"]))

            with c2:
                st.metric("7-night total", f"${p['total']:,.0f}")
                st.write(f"Optimizer score: **{p['score']}/100**")
                if p.get("review_rating") is not None:
                    st.write(f"Guest rating: **{p['review_rating']:.1f}/10**")
                if p.get("star_rating") is not None:
                    stars = p["star_rating"]
                    st.write(f"Hotel class: **{stars:g}/5 stars**")

            with c3:
                st.write(
                    "**Cancellation:** " +
                    ("Refundable" if p["refundable"] else "Non-refundable / verify")
                )
                st.write("**Source:** Nuitee Connect")
                if p["hotel_id"]:
                    st.caption(f"Hotel ID: {p['hotel_id']}")

    st.divider()
    st.subheader("What we're doing now")
    st.write(
        "The optimizer now groups room rates by property, enriches each property with Nuitee's "
        "hotel-content data, and applies the user's selected room-configuration preference. "
        "Required filters out results without bedroom evidence; Preferred keeps them but favors "
        "better bedroom configurations; No preference ignores bedroom configuration. "
        "The next major layer is rewards/platform comparison."
    )

else:
    st.info("Enter the trip and Nuitee sandbox key, then choose **Search & rank**.")
