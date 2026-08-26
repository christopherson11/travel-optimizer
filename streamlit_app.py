import requests
import streamlit as st
from datetime import date
from uuid import uuid4

st.set_page_config(page_title="Travel Optimizer", page_icon="✈️", layout="wide")

API_BASE = "https://api.liteapi.travel/v3.0"

def call_rates(payload, key):
    response = requests.post(
        f"{API_BASE}/hotels/rates",
        json=payload,
        headers={
            "X-API-Key": key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        timeout=30,
    )

    if response.status_code == 401:
        raise RuntimeError(
            "Nuitee rejected the API key (401 Unauthorized). "
            "Make sure you are using the SANDBOX key that starts with 'sand_', "
            "not the sandbox public key."
        )
    if response.status_code == 204:
        return {"data": []}
    if not response.ok:
        try:
            detail = response.json()
        except Exception:
            detail = response.text
        raise RuntimeError(f"Nuitee returned HTTP {response.status_code}: {detail}")

    return response.json()

def price_score(total, budget):
    if total <= budget:
        return 70 + 30 * (budget - total) / budget
    if total <= budget * 1.20:
        return 70 - 40 * (total - budget) / (budget * 0.20)
    return max(0, 30 - 60 * (total - budget * 1.20) / budget)

def score(row, budget):
    quality = min(100, max(0, row.get("rating", 3.75) * 20))
    cancellation = 95 if row.get("refundable") else 50

    # Phase 1 philosophy. Some fields remain conservative until we add
    # property-content and room-mapping data.
    return round(
        0.30 * price_score(row["total"], budget)
        + 0.20 * quality
        + 0.15 * 75      # location: later, village/mountain aware
        + 0.15 * 75      # room configuration: verify private bedroom
        + 0.10 * cancellation
        + 0.05 * 55      # amenities: later from hotel content
        + 0.05 * 55,     # loyalty: later from member-rate searches
        1,
    )

def money_from_rate(rate):
    retail = rate.get("retailRate") or {}
    totals = retail.get("total") or []
    if totals:
        item = totals[0] or {}
        try:
            return float(item.get("amount")), item.get("currency", "USD")
        except (TypeError, ValueError):
            pass

    # Fallbacks for alternate response shapes.
    for key in ("total", "amount"):
        try:
            return float(rate[key]), "USD"
        except (KeyError, TypeError, ValueError):
            pass

    return None, "USD"

def cancellation_info(rate):
    cp = rate.get("cancellationPolicies") or {}
    tag = str(cp.get("refundableTag") or "").upper()
    refundable = tag == "RFN"

    # Some responses expose cancellation info as an array.
    details = cp.get("cancelPolicyInfos") or cp.get("policies") or []
    if isinstance(details, dict):
        details = [details]

    return refundable, details

def flatten(raw):
    rows = []
    for hotel in raw.get("data", []) or []:
        hotel_data = hotel.get("hotelData") or {}

        name = (
            hotel_data.get("name")
            or hotel.get("name")
            or f"Hotel {hotel.get('hotelId', '')}"
        )

        rating_raw = (
            hotel_data.get("rating")
            or hotel_data.get("starRating")
            or hotel.get("rating")
            or 3.75
        )
        try:
            rating = float(rating_raw)
        except (TypeError, ValueError):
            rating = 3.75

        room_types = hotel.get("roomTypes") or []

        # Some response versions can return rates directly.
        if not room_types and hotel.get("rates"):
            room_types = [{"rates": hotel.get("rates")}]

        for room_type in room_types:
            rates = room_type.get("rates") or []

            for rate in rates:
                total, currency = money_from_rate(rate)
                if total is None:
                    continue

                refundable, cancel_details = cancellation_info(rate)

                room_name = (
                    rate.get("name")
                    or rate.get("roomName")
                    or room_type.get("name")
                    or "Room type not supplied"
                )

                rows.append({
                    "name": name,
                    "rating": rating,
                    "room": room_name,
                    "board": rate.get("boardName") or rate.get("board") or "",
                    "total": total,
                    "currency": currency,
                    "refundable": refundable,
                    "cancel_details": cancel_details,
                    "hotel_id": hotel.get("hotelId"),
                    "address": hotel_data.get("address") or hotel.get("address") or {},
                })

    return rows

st.title("✈️ Travel Optimizer")
st.caption("Phase 2 — live lodging-price prototype")

with st.sidebar:
    st.header("Trip")

    destination = st.text_input("Destination", "Stowe, VT")

    checkin = st.date_input("Check-in", date(2026, 12, 5))
    checkout = st.date_input("Check-out", date(2026, 12, 12))

    adults = st.number_input("Adults", min_value=1, max_value=10, value=2)

    child1 = st.number_input("Child 1 age", min_value=0, max_value=17, value=5)
    child2 = st.number_input("Child 2 age", min_value=0, max_value=17, value=7)

    budget = st.number_input(
        "Target lodging budget (USD)",
        min_value=100,
        max_value=20000,
        value=3000,
        step=100,
    )

    nationality = st.selectbox(
        "Guest nationality",
        ["US", "CA"],
        index=0,
    )

    limit = st.slider(
        "Hotels to search",
        min_value=5,
        max_value=100,
        value=30,
    )

    st.divider()
    st.header("Live data connection")
    st.caption(
        "Use your Nuitee SANDBOX API key. "
        "It starts with 'sand_'. Do not use the sandbox public key."
    )

    api_key = st.text_input(
        "Sandbox API key",
        type="password",
    )

    run_search = st.button(
        "🔎 Search live rates",
        type="primary",
        use_container_width=True,
    )

if run_search:
    if checkout <= checkin:
        st.error("Check-out must be after check-in.")
        st.stop()

    if not api_key:
        st.error("Paste your Nuitee sandbox API key.")
        st.stop()

    if not api_key.startswith("sand_"):
        st.warning(
            "That key does not look like the Nuitee sandbox key. "
            "Your sandbox key should start with 'sand_'."
        )

    city = destination.split(",")[0].strip()

    payload = {
        "occupancies": [
            {
                "adults": int(adults),
                "children": [int(child1), int(child2)],
            }
        ],
        "currency": "USD",
        "guestNationality": nationality,
        "checkin": checkin.isoformat(),
        "checkout": checkout.isoformat(),

        # Nuitee's current v3 rates endpoint supports a city/country search.
        "cityName": city,
        "countryCode": "US",

        "limit": int(limit),
        "maxRatesPerHotel": 3,
        "roomMapping": True,
        "includeHotelData": True,
        "timeout": 10,

        # Keeps related requests consistent if Nuitee has price consistency
        # enabled on the account.
        "sessionId": str(uuid4()),
    }

    with st.spinner(f"Searching live lodging rates for {destination}..."):
        try:
            raw = call_rates(payload, api_key)
            rows = flatten(raw)

            for row in rows:
                row["score"] = score(row, budget)

            rows.sort(key=lambda x: x["score"], reverse=True)

            st.session_state["rows"] = rows
            st.session_state["search_error"] = None

        except Exception as exc:
            st.session_state["rows"] = []
            st.session_state["search_error"] = str(exc)

error = st.session_state.get("search_error")
if error:
    st.error(f"Live search failed: {error}")

rows = st.session_state.get("rows", [])

if rows:
    st.success(
        f"Live search returned {len(rows)} room-rate options."
    )

    st.warning(
        "Important: this version does NOT assume that a generic hotel "
        "'suite' has a separate bedroom. That remains a hard verification "
        "step until we add richer room/property data."
    )

    for i, row in enumerate(rows[:15], 1):
        with st.container(border=True):
            left, middle, right = st.columns([5, 2, 2])

            with left:
                st.subheader(f"{i}. {row['name']}")
                st.write(f"**Room:** {row['room']}")

                if row["board"]:
                    st.caption(f"Meal plan: {row['board']}")

                st.caption(
                    "Private bedroom requirement: **VERIFY**"
                )

            with middle:
                st.metric(
                    "Stay total",
                    f"${row['total']:,.0f}",
                )
                st.write(f"Optimizer score: **{row['score']}/100**")

            with right:
                st.write(
                    "Cancellation:",
                    "Refundable"
                    if row["refundable"]
                    else "Non-refundable / verify",
                )
                st.write("Source: **Nuitee Connect**")

    st.divider()
    st.subheader("Phase 2 status")
    st.write(
        "✅ Trip parameters are being sent to a live lodging API.  "
        "The next layers are room/bedroom verification, vacation rentals, "
        "loyalty/member pricing, and Chase/award comparisons."
    )

else:
    st.info(
        "Enter the trip and your Nuitee sandbox key, then choose "
        "**Search live rates**."
    )
