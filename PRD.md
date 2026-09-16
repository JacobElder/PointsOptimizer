### Technical Spec: Stochastic Valuation Engine

**Objective:** Determine the mathematical Expected Value (EV) of redeeming points today versus hoarding them for future trips, factoring in stochastic travel demand, deterministic point devaluation, and the opportunity cost of cash.

#### 1. Mathematical Framework

We need to calculate the true cost of holding points over time.

**Point Devaluation (Inflation)**

Airlines devalue their award charts regularly. The future value of a point at time $t$ is:

$$V_{t}=V_{0}(1-d)^t \approx V_{0}e^{-dt}$$

Where $V_{0}$ is the baseline Cent Per Point (CPP) value today, $d$ is the estimated annual devaluation rate (e.g., 0.06 for 6%), and $t$ is time in years. The implementation uses the continuous form $e^{-dt}$, which stays well-defined for any rate.

**Opportunity Cost of Capital**

If a user pays cash today to hoard points, that cash could have been invested. The future value of that spent cash is:

$$C_{FV}=C_{today}(1+r)^t$$

Where $r$ is the expected annual market return (default 0.05). The implementation discounts future redemptions by $e^{-(d+r)t}$.

#### 2. Simulation Inputs (State Variables)

To run the simulation, the model requires the following parameters:

- `current_cpp`: CPP of the redemption being evaluated.
- `point_balance`: points in the pool being considered (validated; see step 5 for why it doesn't move the estimate).
- `cash_price`: typical cash value of a future high-value trip (NOT the evaluated deal's own price).
- `time_horizon`: Number of years to simulate (standardize to 3 or 5 years).
- `lambda_trips`: The expected number of high-value redemptions per year, modeled as a Poisson parameter $\lambda$.
- `mu_cost` and `sigma_cost`: Parameters for the distribution of future flight costs in points, best modeled as a Log-Normal distribution to prevent negative costs and account for right-skewed premium cabin pricing.
- `depreciation_rate`: The $d$ variable (default to 0.05).
- `market_return`: The $r$ variable (default to 0.05 to account for inflation-adjusted conservative returns).

#### 3. Execution Logic (The Monte Carlo Loop)

The engine will use NumPy or Pandas to run the simulations efficiently.

1. Initialize $N=10000$ iterations.
2. For each iteration, loop through the years in the `time_horizon`.
3. Draw the number of trips taken in year $t$ from $\text{Poisson}(\lambda)$.
4. For each trip, draw the required points cost from $\text{LogNormal}(\mu,\sigma)$.
5. Value every drawn trip at its own CPP (`cash_price / points`), independent of the balance. Rules that condition on affordability bias the result: crediting a partial balance at the full trip's CPP, or redeeming only trips that fit, made the same deal score anywhere from 1.9¢ to 2.7¢ depending on balance size (found 2026-09-16).
6. Discount each trip's cash value to today by $e^{-(d+r)t}$.
7. Per iteration, take the points-weighted average discounted CPP; average across iterations (iterations with no trips use the closed-form expected value).

#### 4. Decision Rule Output

- **Average Simulated CPP:** The mean discounted future value of a point across all 10,000 simulations (plus 5th/95th percentiles).
- **Actionable Output:** If the CPP of the *current* redemption is strictly greater than the Average Simulated CPP, the tool recommends **Redeem**; otherwise **Hoard**.

