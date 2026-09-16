# Everything runs in the project .venv (fast-flights needs a newer protobuf than the
# base Anaconda env can take). `make setup` once, then use the targets below.
PY := .venv/bin/python

.PHONY: setup app find find-quiet price test check

setup:            ## create .venv with app + pipeline deps
	python3 -m venv .venv && $(PY) -m pip install -q -r requirements.txt pytest

app:              ## run the Streamlit app
	$(PY) -m streamlit run app.py

find:             ## scan seats.aero, price top candidates, email new standouts
	$(PY) deal_finder.py

find-quiet:       ## same, no email
	$(PY) deal_finder.py --no-email

price:            ## price the Gmail-captured pending queue (commits + pushes on main)
	$(PY) price_pending_deals.py --no-email

test:
	$(PY) -m pytest -q

check:            ## confirm free cash-price lookups work from this machine
	$(PY) cash_price_check.py
