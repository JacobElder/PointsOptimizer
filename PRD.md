### Technical Spec: Stochastic Valuation Engine

**Objective:** Determine the mathematical Expected Value (EV) of redeeming points today versus hoarding them for future trips, factoring in stochastic travel demand, deterministic point devaluation, and the opportunity cost of cash.

#### 1. Mathematical Framework

We need to calculate the true cost of holding points over time.

**Point Devaluation (Inflation)**

Airlines devalue their award charts regularly. The future value of a point at time $t$ is:

$$V_{t}=V_{0}(1-d)^t$$

Where $V_{0}$ is the baseline Cent Per Point (CPP) value today, $d$ is the estimated annual devaluation rate (e.g., 0.06 for 6%), and $t$ is time in years.

**Opportunity Cost of Capital**

If a user pays cash today to hoard points, that cash could have been invested. The future value of that spent cash is:

$$C_{FV}=C_{today}(1+r)^t$$

Where $r$ is the expected annual market return (e.g., 0.07).

#### 2. Simulation Inputs (State Variables)

To run the simulation, the model requires the following parameters:

- `current_balances`: Dictionary or array of point totals (e.g., Chase: 150k, Bilt: 40k).
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
5. Deduct points from the starting balance. If the balance hits zero, the remaining trips are paid in cash.
6. Apply the devaluation formula to the points used in year $t$ to calculate their actual realized value.
7. Aggregate the Net Present Value (NPV) of all simulated redemptions across all iterations.

#### 4. Decision Rule Output

- **Average Simulated NPV:** The mean value of hoarding the points across all 10,000 simulations.
- **Actionable Output:** If the calculated CPP of the *current* target route is strictly greater than the Average Simulated NPV, the tool recommends **Redeem**. If it is lower, the tool recommends **Hoard**.

