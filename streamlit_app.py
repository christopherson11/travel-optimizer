import os, requests, streamlit as st
from datetime import date

st.set_page_config(page_title='Travel Optimizer', page_icon='✈️', layout='wide')
API_BASE='https://api.liteapi.travel/v3.0'

def call(path,payload,key):
    r=requests.post(API_BASE+path,json=payload,headers={'X-API-Key':key,'Content-Type':'application/json'},timeout=45)
    r.raise_for_status(); return r.json()

def price_score(total,budget):
    if total<=budget: return 70+30*(budget-total)/budget
    if total<=budget*1.2: return 70-40*(total-budget)/(budget*.2)
    return max(0,30-60*(total-budget*1.2)/budget)

def score(r,budget):
    quality=r.get('rating',7.5)*10
    cancel=95 if r.get('refundable') else 50
    return round(.30*price_score(r['total'],budget)+.20*quality+.15*75+.15*75+.10*cancel+.05*55+.05*55,1)

def flatten(raw):
    rows=[]
    for h in raw.get('data',[]):
        hd=h.get('hotelData') or {}
        name=hd.get('name') or h.get('name') or f"Hotel {h.get('hotelId','')}"
        rating=hd.get('rating') or hd.get('starRating') or 7.5
        try: rating=float(rating)
        except: rating=7.5
        for rt in h.get('roomTypes',[]):
            for rate in rt.get('rates',[]):
                totals=(rate.get('retailRate') or {}).get('total') or []
                if not totals: continue
                try: total=float(totals[0].get('amount'))
                except: continue
                cp=rate.get('cancellationPolicies') or {}
                rows.append({'name':name,'rating':rating,'room':rate.get('name') or 'Room type not supplied','board':rate.get('boardName') or '', 'total':total,'currency':totals[0].get('currency','USD'),'refundable':str(cp.get('refundableTag','')).upper()=='RFN'})
    return rows

st.title('✈️ Travel Optimizer')
st.caption('Phase 2 — live lodging-price prototype')
with st.sidebar:
    st.header('Trip')
    destination=st.text_input('Destination','Stowe, VT')
    checkin=st.date_input('Check-in',date(2026,12,5))
    checkout=st.date_input('Check-out',date(2026,12,12))
    adults=st.number_input('Adults',1,10,2)
    child1=st.number_input('Child 1 age',0,17,5)
    child2=st.number_input('Child 2 age',0,17,7)
    budget=st.number_input('Target lodging budget (USD)',100,20000,3000,100)
    country=st.selectbox('Guest nationality',['US','CA'])
    limit=st.slider('Hotels to search',5,100,30)
    st.divider(); st.header('Live data connection')
    st.write('Paste your Nuitee Connect sandbox API key here. It stays in this app session.')
    key=st.text_input('Sandbox API key',type='password')
    run=st.button('🔎 Search live rates',type='primary',use_container_width=True)
if run:
    if checkout<=checkin: st.error('Check-out must be after check-in.'); st.stop()
    if not key: st.error('Paste the sandbox API key.'); st.stop()
    payload={'occupancies':[{'adults':int(adults),'children':[int(child1),int(child2)]}], 'currency':'USD','guestNationality':country,'checkin':checkin.isoformat(),'checkout':checkout.isoformat(),'cityName':destination.split(',')[0].strip(),'countryCode':'US','limit':int(limit),'roomMapping':True,'includeHotelData':True,'maxRatesPerHotel':3}
    with st.spinner('Searching live lodging rates...'):
        try:
            rows=flatten(call('/hotels/rates',payload,key))
            for r in rows:r['score']=score(r,budget)
            rows.sort(key=lambda x:x['score'],reverse=True)
            st.session_state.rows=rows
        except Exception as e: st.error(f'Live search failed: {e}')
rows=st.session_state.get('rows',[])
if rows:
    st.success(f'Live search returned {len(rows)} room-rate options.')
    st.warning('The live API gives us room names and rates, but we will not assume a generic “suite” means a separate bedroom. That remains a finalist verification step.')
    for i,r in enumerate(rows[:15],1):
        with st.container(border=True):
            a,b,c=st.columns([5,2,2])
            with a:
                st.subheader(f'{i}. {r["name"]}')
                st.write(f'**Room:** {r["room"]}')
                if r['board']: st.caption(f'Meal plan: {r["board"]}')
                st.caption('Private bedroom: **VERIFY**')
            with b:
                st.metric('Stay total',f'${r["total"]:,.0f}')
                st.write(f'Score: **{r["score"]}/100**')
            with c:
                st.write('Cancellation:', 'Refundable' if r['refundable'] else 'Non-refundable / verify')
                st.write('Source: **Nuitee Connect**')
else: st.info('Enter the trip and API key, then search.')
