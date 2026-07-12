# PointsOptimizer

## Usage

**Streamlit app (recommended):**
```bash
cd ~/Documents/GitHub/PointsOptimizer
streamlit run app.py
```



**Run a simulation:**
```bash
cd ~/Documents/GitHub/PointsOptimizer
python3 -c "
from simulation import run_valuation_simulation
result = run_valuation_simulation(current_cpp=1.5, point_balance=100_000, cash_price=1_500)
print(result)
"
```

**Or drop into an interactive shell:**
```bash
python3
>>> from simulation import run_valuation_simulation
>>> result = run_valuation_simulation(current_cpp=1.5, point_balance=100_000, cash_price=1_500)
>>> result.recommend_redeem
>>> result.avg_simulated_cpp
```

**Run the tests:**
```bash
python -m pytest test_simulation.py -v
```

